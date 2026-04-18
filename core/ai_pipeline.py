"""
BlindGuide — AI Pipeline
Scene description (Gemini Vision) and voice output (ElevenLabs TTS).
Conversation mode for follow-up questions.

Can be tested RIGHT NOW on your laptop with just API keys.
Set environment variables:
    export GEMINI_API_KEY="your-key"
    export ELEVENLABS_API_KEY="your-key"
"""

import os
import io
import base64
import asyncio
import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("AIPipeline")

# ─── Gemini Setup ──────────────────────────────────────────────────

try:
    from google import genai
    from google.genai import types
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    logger.warning("google-genai not installed. Run: pip install google-genai")

# ─── ElevenLabs Setup ──────────────────────────────────────────────

try:
    from elevenlabs import ElevenLabs
    ELEVENLABS_AVAILABLE = True
except ImportError:
    ELEVENLABS_AVAILABLE = False
    logger.warning("elevenlabs not installed. Run: pip install elevenlabs")

# ─── Constants ─────────────────────────────────────────────────────

SCENE_DESCRIPTION_PROMPT = """You are an AI assistant helping a blind person understand their surroundings. 
Describe what you see in this image concisely but thoroughly. Focus on:
1. Immediate hazards (stairs, curbs, obstacles, vehicles, wet floor)
2. Navigable paths (sidewalks, doors, crosswalks, open areas)  
3. Key landmarks and orientation cues (buildings, signs, intersections)
4. People nearby and their approximate positions
5. Any text visible (signs, store names, street names)

Be direct and spatial. Use clock-position references (e.g., "a bench at your 2 o'clock, about 3 meters away"). 
Keep it under 4 sentences for quick situations, up to 8 sentences for complex scenes.
Do NOT say "I see an image of" — speak as if you are their eyes."""

CONVERSATION_SYSTEM_PROMPT = """You are a helpful AI assistant built into a wearable device for a blind person.
You help them navigate, understand their environment, and handle daily tasks.
Keep responses concise and spoken-word friendly (no markdown, no bullet points, no special characters).
Use spatial language and be specific about directions.
If they ask about what's around them, reference the most recent scene description if available.
If they ask for navigation help, provide clear step-by-step directions.
Always prioritize safety information."""

# ElevenLabs voice config
VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"  # "George" - clear male voice, good for assistive
# Other options: "21m00Tcm4TlvDq8ikWAM" (Rachel), "EXAVITQu4vr4xnSDxMaL" (Bella)
VOICE_MODEL = "eleven_turbo_v2_5"  # fastest model


class AIPipeline:
    def __init__(self):
        self.gemini_client = None
        self.elevenlabs_client = None
        self.conversation_history = []
        self.last_scene_description = ""

        self._init_gemini()
        self._init_elevenlabs()

    def _init_gemini(self):
        if not GEMINI_AVAILABLE:
            return
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            logger.error("No GEMINI_API_KEY set. Scene description won't work.")
            return
        self.gemini_client = genai.Client(api_key=api_key)
        logger.info("Gemini client initialized")

    def _init_elevenlabs(self):
        if not ELEVENLABS_AVAILABLE:
            return
        api_key = os.environ.get("ELEVENLABS_API_KEY")
        if not api_key:
            logger.error("No ELEVENLABS_API_KEY set. Voice output won't work.")
            return
        self.elevenlabs_client = ElevenLabs(api_key=api_key)
        logger.info("ElevenLabs client initialized")

    # ─── Scene Description ─────────────────────────────────────────

    def describe_scene(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        """
        Send an image to Gemini Vision and get a scene description.
        
        Args:
            image_bytes: Raw image bytes (JPEG or PNG)
            mime_type: Image MIME type
            
        Returns:
            Text description of the scene
        """
        if not self.gemini_client:
            logger.error("Gemini not available")
            return "Scene description is not available right now."

        start = time.time()
        try:
            response = self.gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                    SCENE_DESCRIPTION_PROMPT,
                ],
            )
            description = response.text
            elapsed = time.time() - start
            logger.info(f"Scene described in {elapsed:.1f}s: {description[:80]}...")

            self.last_scene_description = description
            # Add to conversation context
            self.conversation_history.append({
                "role": "user",
                "content": "[Scene captured by camera]"
            })
            self.conversation_history.append({
                "role": "assistant", 
                "content": description
            })
            # Keep history manageable
            if len(self.conversation_history) > 20:
                self.conversation_history = self.conversation_history[-20:]

            return description

        except Exception as e:
            logger.error(f"Gemini scene description failed: {e}")
            return "I had trouble analyzing the scene. Please try again."

    def describe_scene_from_file(self, image_path: str) -> str:
        """Convenience method for testing with image files."""
        path = Path(image_path)
        mime = "image/jpeg" if path.suffix.lower() in [".jpg", ".jpeg"] else "image/png"
        return self.describe_scene(path.read_bytes(), mime)

    # ─── Conversation Mode ─────────────────────────────────────────

    def chat(self, user_message: str) -> str:
        """
        Have a conversation with the AI, maintaining context.
        
        Args:
            user_message: What the user said (transcribed from speech)
            
        Returns:
            AI response text
        """
        if not self.gemini_client:
            return "Conversation is not available right now."

        self.conversation_history.append({
            "role": "user",
            "content": user_message
        })

        # Build Gemini contents from history
        contents = []
        for msg in self.conversation_history:
            role = "user" if msg["role"] == "user" else "model"
            contents.append(types.Content(
                role=role,
                parts=[types.Part.from_text(text=msg["content"])]
            ))

        start = time.time()
        try:
            response = self.gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=CONVERSATION_SYSTEM_PROMPT,
                    max_output_tokens=200,  # keep responses concise for voice
                    temperature=0.7,
                ),
            )
            reply = response.text
            elapsed = time.time() - start
            logger.info(f"Chat response in {elapsed:.1f}s: {reply[:80]}...")

            self.conversation_history.append({
                "role": "assistant",
                "content": reply
            })

            return reply

        except Exception as e:
            logger.error(f"Gemini chat failed: {e}")
            return "I had trouble understanding. Could you repeat that?"

    # ─── Voice Output ──────────────────────────────────────────────

    def speak(self, text: str) -> Optional[bytes]:
        """
        Convert text to speech using ElevenLabs.
        
        Returns:
            Audio bytes (mp3) or None if failed.
            On the UNO Q, pipe these to `aplay` or `mpv`.
        """
        if not self.elevenlabs_client:
            logger.warning(f"ElevenLabs not available. Would say: {text}")
            return None

        start = time.time()
        try:
            audio = self.elevenlabs_client.text_to_speech.convert(
                text=text,
                voice_id=VOICE_ID,
                model_id=VOICE_MODEL,
                output_format="mp3_22050_32",  # small file, good enough quality
            )

            # audio is a generator, collect all chunks
            audio_bytes = b"".join(audio)
            elapsed = time.time() - start
            logger.info(f"TTS generated in {elapsed:.1f}s, {len(audio_bytes)} bytes")
            return audio_bytes

        except Exception as e:
            logger.error(f"ElevenLabs TTS failed: {e}")
            return None

    def speak_and_play(self, text: str):
        """Speak text and immediately play it. Works on laptop for testing."""
        audio = self.speak(text)
        if audio:
            self._play_audio(audio)
        else:
            print(f"[WOULD SAY]: {text}")

    def _play_audio(self, audio_bytes: bytes):
        """Play audio bytes. Cross-platform fallback."""
        import tempfile
        import subprocess

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name

        try:
            # Try different players
            for player in ["mpv", "afplay", "aplay", "ffplay"]:
                try:
                    subprocess.run(
                        [player, "--no-video", tmp_path] if player == "mpv" 
                        else [player, tmp_path],
                        capture_output=True, timeout=30
                    )
                    return
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    continue
            logger.warning("No audio player found. Install mpv or ffplay.")
        finally:
            os.unlink(tmp_path)

    # ─── Full Pipeline ─────────────────────────────────────────────

    def capture_describe_speak(self, image_bytes: bytes) -> str:
        """
        Full pipeline: image → description → speech.
        This is what gets called when the user triggers "describe" gesture.
        """
        description = self.describe_scene(image_bytes)
        self.speak_and_play(description)
        return description


# ─── Test without hardware ─────────────────────────────────────────

if __name__ == "__main__":
    pipeline = AIPipeline()

    print("=== Test 1: Scene description from file ===")
    # Create a simple test — use any image on your laptop
    test_image = Path("test_scene.jpg")
    if test_image.exists():
        desc = pipeline.describe_scene_from_file(str(test_image))
        print(f"Description: {desc}")
    else:
        print("No test_scene.jpg found. Place any image as test_scene.jpg to test.")
        print("Testing with Gemini text-only instead...")
        if pipeline.gemini_client:
            response = pipeline.gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents="Say 'Gemini is connected and working' in exactly those words.",
            )
            print(f"Gemini test: {response.text}")

    print("\n=== Test 2: Conversation ===")
    if pipeline.gemini_client:
        reply = pipeline.chat("What should I be aware of when crossing a street?")
        print(f"AI: {reply}")

        reply = pipeline.chat("What about if it's raining?")
        print(f"AI: {reply}")

    print("\n=== Test 3: TTS ===")
    pipeline.speak_and_play("System initialized. BlindGuide is ready.")

    print("\nAll tests complete.")
