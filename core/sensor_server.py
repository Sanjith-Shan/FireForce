"""
SolSpecs — Sensor HTTP Server
Flask app that exposes live biometric data, fire config, the HUD, and fuel analysis.

Reads all computed values from a StateMachine via get_current_state().
Fuel analysis uses the AIPipeline's Gemini client when available.
"""

import json
import logging
import os
import threading
import time

from flask import Flask, jsonify, request, send_from_directory

import config

logger = logging.getLogger("SensorServer")

app = Flask(__name__)

_sm = None          # StateMachine instance
_ai = None          # AIPipeline instance (optional)
_sm_lock = threading.Lock()
_ai_lock = threading.Lock()


def set_state_machine(sm):
    global _sm
    with _sm_lock:
        _sm = sm


def set_ai_pipeline(ai_pipeline):
    global _ai
    with _ai_lock:
        _ai = ai_pipeline


# ── CORS ─────────────────────────────────────────────────────────────────────

def _cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


@app.after_request
def add_cors(response):
    return _cors(response)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/sensors")
def sensors():
    with _sm_lock:
        sm = _sm
    if sm is None:
        # Fallback defaults when state machine not yet attached
        return jsonify({
            "heart_rate": 72, "spo2": 98, "skin_temp_c": 36.5,
            "ambient_temp_c": 28.0, "ambient_humidity_pct": 60.0,
            "wbgt": 25.0, "heat_tier": "green", "hydration": "ok",
            "sweat_level": 0, "gsr": 450,
            "accel_x": 0.0, "accel_y": 0.0, "accel_z": -1.0,
            "fall_detected": False, "sun_exposure_min": 0,
            "noise_exposure_min": 0, "thermal_exposure_s": 0,
            "timestamp": int(time.time()),
        })
    return jsonify(sm.get_current_state())


@app.route("/status")
def status():
    with _sm_lock:
        sm = _sm
    if sm is None:
        return jsonify({"heat_tier": "green", "alerts": [],
                        "thermal_exposure_s": 0, "air_remaining_s": 1800})
    state = sm.get_current_state()
    thermal_s = state["thermal_exposure_s"]
    return jsonify({
        "heat_tier": state["heat_tier"],
        "alerts": _active_alerts(state),
        "thermal_exposure_s": thermal_s,
        "air_remaining_s": max(0, 1800 - thermal_s),
    })


@app.route("/fire-config")
def fire_config():
    return jsonify({
        "wind_direction": 225,
        "wind_speed": 15,
        "grid_size": config.FIRE_GRID_SIZE,
        "tick_rate": config.FIRE_TICK_RATE,
    })


# ── Fuel analysis ─────────────────────────────────────────────────────────────

_FUEL_PROMPT = (
    "You are a wildfire fuel assessment AI assisting a firefighter building a firebreak. "
    "Analyze this ground-level image.\n\n"
    "Identify every visible fuel source that could feed a wildfire. For each, return:\n"
    "- fuel_type: \"dead_grass\", \"pine_needle_litter\", \"dead_brush\", \"fallen_branches\", "
    "\"chaparral\", \"living_brush\", \"small_trees\", or \"large_trees\"\n"
    "- flammability: \"EXTREME\", \"HIGH\", \"MODERATE\", or \"LOW\"\n"
    "- priority: 1-8 (1=clear first: dead grass/needles, 3-4: dead brush/branches, "
    "5-6: chaparral/living brush, 7-8: trees)\n"
    "- box_2d: [ymin, xmin, ymax, xmax] normalized 0-1000\n"
    "- position: natural description (\"3 meters ahead, slightly left\")\n"
    "- action: what to do (\"scrape to mineral soil\", \"cut and remove\", \"fell with chainsaw\")\n\n"
    "Return ONLY a JSON array sorted by priority. No markdown, no explanation."
)


@app.route("/analyze-fuel", methods=["POST", "OPTIONS"])
def analyze_fuel():
    if request.method == "OPTIONS":
        return "", 204

    image_bytes = request.data
    if not image_bytes:
        return jsonify([])

    # Prefer the injected AIPipeline's already-initialised Gemini client
    with _ai_lock:
        ai = _ai

    gemini_client = None
    if ai is not None and getattr(ai, "gemini_client", None) is not None:
        gemini_client = ai.gemini_client
    elif config.GEMINI_API_KEY:
        try:
            from google import genai
            gemini_client = genai.Client(api_key=config.GEMINI_API_KEY)
        except Exception as exc:
            logger.warning("Could not create Gemini client: %s", exc)

    if gemini_client is None:
        return jsonify([])

    try:
        from google.genai import types
        response = gemini_client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                _FUEL_PROMPT,
            ],
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        return jsonify(json.loads(response.text))
    except Exception as exc:
        logger.warning("analyze-fuel error: %s", exc)
        return jsonify([])


# ── HUD static files ──────────────────────────────────────────────────────────

def _hud_dir() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "hud"))


@app.route("/")
@app.route("/hud")
def hud_index():
    return send_from_directory(_hud_dir(), "index.html")


@app.route("/hud/<path:filename>")
def hud_static(filename):
    return send_from_directory(_hud_dir(), filename)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _active_alerts(state: dict) -> list:
    alerts = []
    tier = state.get("heat_tier", "green")
    if tier == "red":
        alerts.append("DANGER — HEAT STRESS CRITICAL — PULL BACK")
    elif tier == "orange":
        alerts.append("HEAT STRESS WARNING — TAKE SHADE BREAK")
    elif tier == "yellow":
        alerts.append("HEAT STRESS CAUTION — HYDRATE NOW")
    if state.get("heart_rate", 0) > 140:
        alerts.append("HEART RATE CRITICAL")
    if state.get("spo2", 100) < 95:
        alerts.append("LOW BLOOD OXYGEN")
    if state.get("fall_detected"):
        alerts.append("FALL DETECTED")
    return alerts


# ── Entry point ───────────────────────────────────────────────────────────────

def run(host: str = "0.0.0.0", port: int = None,
        ssl_context=None, debug: bool = False):
    port = port or config.SENSOR_SERVER_PORT
    app.run(host=host, port=port, ssl_context=ssl_context,
            debug=debug, use_reloader=False)
