"""
Tests for core/sensor_server.py and StateMachine.get_current_state().
Uses Flask test client — no real network needed.
Run with:  pytest tests/test_sensor_server.py -v
"""

import json
import math
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import config
from core.sensor_server import app, set_state_machine
from core.state_machine import StateMachine


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def sm():
    machine = StateMachine()
    machine._TIER_DEBOUNCE = 1
    set_state_machine(machine)
    yield machine
    set_state_machine(None)


@pytest.fixture()
def client(sm):
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture()
def client_no_sm():
    """Client with no state machine attached — tests fallback paths."""
    set_state_machine(None)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


_NORMAL_MCU = {
    "heart_rate": 72, "spo2": 98, "skin_temp_raw": 620,
    "sweat_raw": 0, "gsr_raw": 450,
    "accel_x": 0.0, "accel_y": 0.0, "accel_z": 1.0,
    "emg_raw": 512,
}

_NORMAL_GLASSES = {
    "ambient_temp_c": 24.0, "ambient_humidity_pct": 55.0,
    "is_direct_sun": False, "noise_above_threshold": False,
}

_HOT_MCU = {**_NORMAL_MCU, "heart_rate": 148, "spo2": 93, "skin_temp_raw": 480}
_HOT_GLASSES = {"ambient_temp_c": 38.0, "ambient_humidity_pct": 75.0,
                "is_direct_sun": True, "noise_above_threshold": False}


# ── /sensors ──────────────────────────────────────────────────────────────────

class TestSensorsRoute:

    def test_returns_200(self, client):
        assert client.get("/sensors").status_code == 200

    def test_returns_json_dict(self, client):
        data = json.loads(client.get("/sensors").data)
        assert isinstance(data, dict)

    def test_all_required_keys_present(self, client):
        data = json.loads(client.get("/sensors").data)
        for key in (
            "heart_rate", "spo2", "skin_temp_c", "ambient_temp_c",
            "ambient_humidity_pct", "wbgt", "heat_tier", "hydration",
            "sweat_level", "gsr", "accel_x", "accel_y", "accel_z",
            "fall_detected", "sun_exposure_min", "noise_exposure_min",
            "thermal_exposure_s", "timestamp",
        ):
            assert key in data, f"Missing key: {key}"

    def test_heat_tier_is_valid(self, client):
        data = json.loads(client.get("/sensors").data)
        assert data["heat_tier"] in ("green", "yellow", "orange", "red")

    def test_fall_detected_is_bool(self, client):
        data = json.loads(client.get("/sensors").data)
        assert isinstance(data["fall_detected"], bool)

    def test_wbgt_is_finite(self, client):
        data = json.loads(client.get("/sensors").data)
        assert math.isfinite(data["wbgt"])

    def test_thermal_exposure_nonneg(self, client):
        data = json.loads(client.get("/sensors").data)
        assert data["thermal_exposure_s"] >= 0

    def test_reflects_fed_mcu_heart_rate(self, client, sm):
        sm.feed_mcu({**_NORMAL_MCU, "heart_rate": 110})
        data = json.loads(client.get("/sensors").data)
        assert data["heart_rate"] == 110

    def test_reflects_fed_mcu_spo2(self, client, sm):
        sm.feed_mcu({**_NORMAL_MCU, "spo2": 94})
        data = json.loads(client.get("/sensors").data)
        assert data["spo2"] == 94

    def test_cors_header_present(self, client):
        r = client.get("/sensors")
        assert r.headers.get("Access-Control-Allow-Origin") == "*"

    def test_fallback_when_no_sm(self, client_no_sm):
        r = client_no_sm.get("/sensors")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data["heat_tier"] == "green"

    def test_tier_escalates_under_stress(self, client, sm):
        for _ in range(5):
            sm.feed_glasses(_HOT_GLASSES)
            sm.feed_mcu(_HOT_MCU)
        data = json.loads(client.get("/sensors").data)
        assert data["heat_tier"] in ("yellow", "orange", "red")


# ── /status ───────────────────────────────────────────────────────────────────

class TestStatusRoute:

    def test_returns_200(self, client):
        assert client.get("/status").status_code == 200

    def test_has_all_expected_keys(self, client):
        data = json.loads(client.get("/status").data)
        for key in ("heat_tier", "alerts", "thermal_exposure_s", "air_remaining_s"):
            assert key in data

    def test_alerts_is_list(self, client):
        data = json.loads(client.get("/status").data)
        assert isinstance(data["alerts"], list)

    def test_air_remaining_nonneg(self, client):
        data = json.loads(client.get("/status").data)
        assert data["air_remaining_s"] >= 0

    def test_air_remaining_max_1800(self, client):
        data = json.loads(client.get("/status").data)
        assert data["air_remaining_s"] <= 1800

    def test_critical_conditions_produce_alert(self, client, sm):
        for _ in range(5):
            sm.feed_glasses(_HOT_GLASSES)
            sm.feed_mcu(_HOT_MCU)
        data = json.loads(client.get("/status").data)
        if sm.current_tier == "red":
            assert len(data["alerts"]) > 0

    def test_no_sm_fallback(self, client_no_sm):
        assert client_no_sm.get("/status").status_code == 200

    def test_cors_header_present(self, client):
        r = client.get("/status")
        assert r.headers.get("Access-Control-Allow-Origin") == "*"


# ── /fire-config ──────────────────────────────────────────────────────────────

class TestFireConfigRoute:

    def test_returns_200(self, client):
        assert client.get("/fire-config").status_code == 200

    def test_has_required_keys(self, client):
        data = json.loads(client.get("/fire-config").data)
        for key in ("wind_direction", "wind_speed", "grid_size", "tick_rate"):
            assert key in data

    def test_wind_direction_in_range(self, client):
        data = json.loads(client.get("/fire-config").data)
        assert 0 <= data["wind_direction"] < 360

    def test_grid_size_matches_config(self, client):
        data = json.loads(client.get("/fire-config").data)
        assert data["grid_size"] == config.FIRE_GRID_SIZE

    def test_tick_rate_positive(self, client):
        data = json.loads(client.get("/fire-config").data)
        assert data["tick_rate"] > 0


# ── HUD static routes ─────────────────────────────────────────────────────────

class TestHUDRoutes:

    def test_root_returns_html(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert b"<html" in r.data.lower() or b"<!doctype" in r.data.lower()

    def test_hud_path_returns_html(self, client):
        r = client.get("/hud")
        assert r.status_code == 200
        assert b"SOLSPECS" in r.data or b"solspecs" in r.data.lower()

    def test_fire_simulation_js_served(self, client):
        r = client.get("/hud/fire_simulation.js")
        assert r.status_code == 200
        assert b"FireGrid" in r.data

    def test_fire_simulation_js_content_type(self, client):
        r = client.get("/hud/fire_simulation.js")
        assert "javascript" in r.content_type or "text" in r.content_type

    def test_unknown_file_returns_404(self, client):
        r = client.get("/hud/does_not_exist_xyz.js")
        assert r.status_code == 404

    def test_hud_index_html_explicit(self, client):
        r = client.get("/hud/index.html")
        assert r.status_code == 200


# ── /analyze-fuel ─────────────────────────────────────────────────────────────

class TestAnalyzeFuelRoute:

    def test_options_preflight_returns_204(self, client):
        r = client.options("/analyze-fuel")
        assert r.status_code == 204

    def test_empty_body_returns_empty_list(self, client):
        r = client.post("/analyze-fuel", data=b"")
        assert r.status_code == 200
        assert json.loads(r.data) == []

    def test_no_api_key_returns_empty_list(self, client):
        original = config.GEMINI_API_KEY
        config.GEMINI_API_KEY = ""
        try:
            r = client.post("/analyze-fuel", data=b"\xff\xd8fake_jpeg_bytes\xff\xd9")
            assert r.status_code == 200
            assert json.loads(r.data) == []
        finally:
            config.GEMINI_API_KEY = original

    def test_cors_allows_post(self, client):
        r = client.options("/analyze-fuel")
        assert "POST" in r.headers.get("Access-Control-Allow-Methods", "")

    def test_returns_json_list_type(self, client):
        # With no key, result is empty list — but must be a list, not an error
        r = client.post("/analyze-fuel", data=b"\xff\xd8test\xff\xd9")
        data = json.loads(r.data)
        assert isinstance(data, list)


# ── StateMachine.get_current_state() ─────────────────────────────────────────

class TestGetCurrentState:

    def test_returns_dict(self):
        sm = StateMachine()
        assert isinstance(sm.get_current_state(), dict)

    def test_all_keys_present(self):
        sm = StateMachine()
        state = sm.get_current_state()
        required = [
            "heart_rate", "spo2", "skin_temp_c", "ambient_temp_c",
            "ambient_humidity_pct", "wbgt", "heat_tier", "hydration",
            "sweat_level", "gsr", "accel_x", "accel_y", "accel_z",
            "fall_detected", "sun_exposure_min", "noise_exposure_min",
            "thermal_exposure_s", "timestamp",
        ]
        for k in required:
            assert k in state, f"Missing key: {k}"

    def test_default_tier_is_green(self):
        sm = StateMachine()
        assert sm.get_current_state()["heat_tier"] == "green"

    def test_thermal_exposure_nonneg(self):
        sm = StateMachine()
        assert sm.get_current_state()["thermal_exposure_s"] >= 0

    def test_wbgt_is_finite(self):
        sm = StateMachine()
        state = sm.get_current_state()
        assert math.isfinite(state["wbgt"])

    def test_reflects_fed_heart_rate(self):
        sm = StateMachine()
        sm.feed_mcu({**_NORMAL_MCU, "heart_rate": 105})
        assert sm.get_current_state()["heart_rate"] == 105

    def test_reflects_fed_spo2(self):
        sm = StateMachine()
        sm.feed_mcu({**_NORMAL_MCU, "spo2": 95})
        assert sm.get_current_state()["spo2"] == 95

    def test_reflects_fed_ambient_temp(self):
        sm = StateMachine()
        sm.feed_glasses({**_NORMAL_GLASSES, "ambient_temp_c": 35.0})
        state = sm.get_current_state()
        assert state["ambient_temp_c"] == 35.0

    def test_skin_temp_in_plausible_range(self):
        sm = StateMachine()
        state = sm.get_current_state()
        assert 30.0 < state["skin_temp_c"] < 45.0

    def test_fall_detected_is_bool(self):
        sm = StateMachine()
        assert isinstance(sm.get_current_state()["fall_detected"], bool)

    def test_hydration_is_string(self):
        sm = StateMachine()
        assert isinstance(sm.get_current_state()["hydration"], str)

    def test_sun_exposure_nonneg(self):
        sm = StateMachine()
        assert sm.get_current_state()["sun_exposure_min"] >= 0

    def test_repeated_calls_stable(self):
        sm = StateMachine()
        s1 = sm.get_current_state()
        s2 = sm.get_current_state()
        # Heat tier and most values should be identical between two rapid calls
        assert s1["heat_tier"] == s2["heat_tier"]
        assert s1["heart_rate"] == s2["heart_rate"]

    def test_tier_escalates_after_critical_feed(self):
        sm = StateMachine()
        sm._TIER_DEBOUNCE = 1
        for _ in range(5):
            sm.feed_glasses(_HOT_GLASSES)
            sm.feed_mcu(_HOT_MCU)
        state = sm.get_current_state()
        assert state["heat_tier"] in ("yellow", "orange", "red")
