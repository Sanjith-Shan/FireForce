"""
SolSpecs — Glasses Server
Flask server running on the Raspberry Pi Zero 2W.

Endpoints:
    GET  /sensors  → JSON of all glasses sensor readings
    GET  /capture  → JPEG bytes from Pi Camera
    POST /display  → update OLED with heat tier color + message
    GET  /health   → {"status": "ok"}

Run in live mode:
    python glasses_server.py

Run in simulate mode (no hardware needed):
    python glasses_server.py --simulate
"""

import argparse
import io
import json
import logging
import os
import sys
import time
import threading

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("GlassesServer")

try:
    from flask import Flask, request, jsonify, Response
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    logger.error("flask not installed. Run: pip install flask")
    sys.exit(1)


# ── Parse args early so we know the mode before importing hardware libs ──

parser = argparse.ArgumentParser()
parser.add_argument("--simulate", action="store_true", help="Run without hardware")
parser.add_argument("--port", type=int, default=int(os.environ.get("FLASK_PORT", 5001)))
args, _ = parser.parse_known_args()
SIMULATE = args.simulate


# ── Hardware imports (skipped in simulate mode) ──────────────────────────────

dht_device = None
camera = None
oled_display = None

if not SIMULATE:
    try:
        import board
        import adafruit_dht
        dht_device = adafruit_dht.DHT11(board.D4)
        logger.info("DHT11 initialized on GPIO4")
    except Exception as e:
        logger.warning(f"DHT11 init failed: {e}. Sensor will return None.")

    try:
        from picamera2 import Picamera2
        camera = Picamera2()
        camera.configure(camera.create_still_configuration(
            main={"size": (640, 480), "format": "RGB888"}
        ))
        camera.start()
        time.sleep(1)  # warm-up
        logger.info("Pi Camera initialized")
    except Exception as e:
        logger.warning(f"Camera init failed: {e}. /capture will return placeholder.")

    try:
        from oled_display import OLEDDisplay
        oled_display = OLEDDisplay()
        logger.info("OLED display initialized")
    except Exception as e:
        logger.warning(f"OLED init failed: {e}. Display commands will be logged only.")

    # GPIO pins for photoresistor and sound sensor digital outputs
    try:
        import RPi.GPIO as GPIO
        GPIO.setmode(GPIO.BCM)
        PHOTO_PIN = 17   # photoresistor digital threshold output
        SOUND_PIN = 27   # sound sensor digital output (HIGH when loud)
        GPIO.setup(PHOTO_PIN, GPIO.IN)
        GPIO.setup(SOUND_PIN, GPIO.IN)
        logger.info(f"GPIO pins: photo={PHOTO_PIN}, sound={SOUND_PIN}")
        GPIO_AVAILABLE = True
    except Exception as e:
        logger.warning(f"GPIO init failed: {e}")
        GPIO_AVAILABLE = False
else:
    logger.info("Running in SIMULATE mode — no hardware access")
    GPIO_AVAILABLE = False


# ── Sensor state ──────────────────────────────────────────────────────────────

_sensor_lock = threading.Lock()
_sensor_cache = {
    "ambient_temp_c": None,
    "ambient_humidity_pct": None,
    "light_level": 0,
    "is_direct_sun": False,
    "noise_level_db": 0,
    "noise_above_threshold": False,
    "timestamp": 0.0,
}

# Simulated state
_sim_tick = 0


def _read_sensors_live():
    """Read all hardware sensors and update _sensor_cache."""
    global _sim_tick
    data = {}

    # DHT11
    if dht_device:
        try:
            data["ambient_temp_c"] = dht_device.temperature
            data["ambient_humidity_pct"] = dht_device.humidity
        except Exception as e:
            logger.debug(f"DHT11 read error: {e}")
            data["ambient_temp_c"] = _sensor_cache.get("ambient_temp_c")
            data["ambient_humidity_pct"] = _sensor_cache.get("ambient_humidity_pct")
    else:
        data["ambient_temp_c"] = None
        data["ambient_humidity_pct"] = None

    # Photoresistor (digital threshold pin)
    if GPIO_AVAILABLE:
        import RPi.GPIO as GPIO
        photo_high = GPIO.input(PHOTO_PIN)
        data["is_direct_sun"] = bool(photo_high)
        data["light_level"] = 900 if photo_high else 200
    else:
        data["is_direct_sun"] = False
        data["light_level"] = 0

    # Sound sensor (digital threshold pin)
    if GPIO_AVAILABLE:
        sound_high = GPIO.input(SOUND_PIN)
        data["noise_above_threshold"] = bool(sound_high)
        data["noise_level_db"] = 90 if sound_high else 60
    else:
        data["noise_above_threshold"] = False
        data["noise_level_db"] = 0

    data["timestamp"] = time.time()
    return data


def _read_sensors_simulate():
    """Generate fake sensor readings that exercise the heat stress algorithm."""
    import random
    global _sim_tick
    _sim_tick += 1

    # Slowly rising temperature to simulate a hot work day
    base_temp = 28.0 + min(_sim_tick * 0.05, 10.0)
    return {
        "ambient_temp_c": round(base_temp + random.uniform(-0.3, 0.3), 1),
        "ambient_humidity_pct": round(62.0 + random.uniform(-2, 2), 1),
        "light_level": 850 + random.randint(-30, 30),
        "is_direct_sun": True,
        "noise_level_db": 75 + random.randint(-5, 5),
        "noise_above_threshold": False,
        "timestamp": time.time(),
    }


def read_sensors() -> dict:
    if SIMULATE:
        return _read_sensors_simulate()
    return _read_sensors_live()


# ── OLED helpers ──────────────────────────────────────────────────────────────

def update_oled(heat_tier: str, message: str = ""):
    if oled_display:
        try:
            oled_display.show_tier(heat_tier, message)
        except Exception as e:
            logger.warning(f"OLED update failed: {e}")
    else:
        logger.info(f"[OLED] tier={heat_tier} msg={message!r}")


# ── Camera helpers ────────────────────────────────────────────────────────────

def capture_jpeg() -> bytes:
    """Capture a frame and return JPEG bytes."""
    if camera:
        try:
            from PIL import Image
            array = camera.capture_array()
            img = Image.fromarray(array)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return buf.getvalue()
        except Exception as e:
            logger.warning(f"Camera capture failed: {e}")

    # Fallback: return a minimal placeholder JPEG
    return _placeholder_jpeg()


def _placeholder_jpeg() -> bytes:
    """1×1 white JPEG for testing when no camera is available."""
    try:
        from PIL import Image
        import io
        img = Image.new("RGB", (640, 480), color=(200, 200, 200))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        return buf.getvalue()
    except ImportError:
        # Absolute minimal JPEG bytes
        return (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
            b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
            b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xf8\xd9"
        )


# ── Flask app ─────────────────────────────────────────────────────────────────

app = Flask(__name__)


@app.route("/health")
def health():
    return jsonify({"status": "ok", "simulate": SIMULATE})


@app.route("/sensors")
def sensors():
    data = read_sensors()
    return jsonify(data)


@app.route("/capture")
def capture():
    jpeg = capture_jpeg()
    return Response(jpeg, mimetype="image/jpeg")


@app.route("/display", methods=["POST"])
def display():
    body = request.get_json(silent=True) or {}
    heat_tier = body.get("heat_tier", "green")
    message = body.get("message", "")

    valid_tiers = {"green", "yellow", "orange", "red"}
    if heat_tier not in valid_tiers:
        return jsonify({"error": f"invalid heat_tier '{heat_tier}'"}), 400

    update_oled(heat_tier, message)
    return jsonify({"ok": True, "heat_tier": heat_tier, "message": message})


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mode = "SIMULATE" if SIMULATE else "LIVE"
    logger.info(f"SolSpecs Glasses Server starting [{mode}] on port {args.port}")
    app.run(host="0.0.0.0", port=args.port, debug=False, threaded=True)
