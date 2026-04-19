"""
SolSpecs — Configuration
All thresholds, URLs, and settings in one place.
"""

import os

# ─── Network ───────────────────────────────────────────────────────
PHONE_GPS_URL = os.environ.get("PHONE_GPS_URL", "http://192.168.1.100:5000")
SENSOR_SERVER_PORT = int(os.environ.get("SENSOR_SERVER_PORT", "8080"))
MODE = os.environ.get("SOLSPECS_MODE", "simulate")  # "simulate" or "live"

# ─── API Keys ──────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
QUALCOMM_AI_API_KEY = os.environ.get("QUALCOMM_AI_API_KEY", "")

# ─── Heat Stress Thresholds ────────────────────────────────────────
# OSHA WBGT thresholds for moderate workload
WBGT_LOW = 24.0       # below this = green
WBGT_MODERATE = 26.0  # yellow territory
WBGT_HIGH = 28.0      # orange territory
WBGT_CRITICAL = 30.0  # red territory

# Tier score thresholds (composite score from all signals)
TIER_YELLOW = 3
TIER_ORANGE = 6
TIER_RED = 10

# ─── Physiological Thresholds ─────────────────────────────────────
HR_MILD = 90
HR_CONCERN = 100
HR_ELEVATED = 120
HR_CRITICAL = 140
HR_ELEVATED_DURATION_S = 30.0

SPO2_MILD = 95
SPO2_CONCERN = 93
SPO2_CRITICAL = 90

SKIN_TEMP_MILD = 37.0
SKIN_TEMP_CONCERN = 37.5
SKIN_TEMP_CRITICAL = 38.5

BREATHING_RATE_HIGH = 25  # breaths per minute
BREATHING_RATE_LOW = 8

# ─── Sun Exposure ─────────────────────────────────────────────────
SUN_EXPOSURE_MILD_MIN = 15
SUN_EXPOSURE_CONCERN_MIN = 30
SUN_EXPOSURE_CRITICAL_MIN = 45
LIGHT_THRESHOLD_DIRECT_SUN = 700  # ADC value, calibrate at venue

# ─── Noise Exposure ───────────────────────────────────────────────
NOISE_THRESHOLD_DB = 85  # OSHA 8-hour limit
NOISE_ALERT_HOURS = 2.0  # alert after this many hours above threshold

# ─── Fall Detection ───────────────────────────────────────────────
FALL_ACCEL_THRESHOLD_G = 3.0
FALL_RESPONSE_TIMEOUT_S = 15.0

# ─── EMG ──────────────────────────────────────────────────────────
EMG_FLEX_THRESHOLD = 300     # ADC, calibrate per user
EMG_SUSTAIN_MS = 800         # hold duration for "scan" gesture
EMG_COOLDOWN_MS = 2000       # min time between gestures

# ─── Polling Intervals ────────────────────────────────────────────
PHONE_GPS_POLL_INTERVAL_S = 5.0
MCU_SENSOR_HZ = 20
FIRE_GRID_SIZE = 64
FIRE_TICK_RATE = 2.0
STATUS_CHECK_GREEN_MIN = 30
STATUS_CHECK_YELLOW_MIN = 10
STATUS_CHECK_ORANGE_MIN = 5

# ─── Audio ────────────────────────────────────────────────────────
ELEVENLABS_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"  # George
ELEVENLABS_MODEL = "eleven_turbo_v2_5"

# ─── Qualcomm Cloud AI (Cirrascale) ──────────────────────────────
QUALCOMM_AI_BASE_URL = "https://aisuite.cirrascale.com/apis/v2"
QUALCOMM_AI_MODEL = "Llama-3.3-70B"              # default: best quality
QUALCOMM_AI_REASONING_MODEL = "DeepSeek-R1-Distill-Llama-70B"  # trend analysis
QUALCOMM_AI_FAST_MODEL = "Llama-3.1-8B"          # fallback if latency matters
QUALCOMM_AI_TIMEOUT = 30.0                        # seconds

# ─── Gemini ───────────────────────────────────────────────────────
GEMINI_MODEL = "gemini-2.5-flash"

SCENE_PROMPT = (
    "You are an AI safety system for a firefighter in an active wildfire zone. "
    "Analyze this image. Report: "
    "1) Visible fire, smoke, or ember activity and direction. "
    "2) Structural integrity of nearby buildings or terrain stability. "
    "3) Egress routes — clear paths away from fire. "
    "4) Hazards — downed power lines, gas lines, unstable structures, flashover risk. "
    "Be concise, under 4 sentences."
)

CONVERSATION_SYSTEM_PROMPT = (
    "You are an AI assistant for a firefighter during active wildfire operations. "
    "You have access to their live biometric data and fire spread predictions. "
    "Help them stay safe. Advise on heat stress, hydration, egress routes, and tactical decisions. "
    "Keep responses under 3 sentences — they're in an active emergency."
)
