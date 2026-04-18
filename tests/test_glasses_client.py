"""
Tests for core/glasses_client.py

Runs fully on laptop — no network, no Pi Zero needed.
Uses MockGlassesClient for integration-style tests and patches requests
for unit tests of the real GlassesClient path.
Run with:  pytest tests/test_glasses_client.py -v
"""

import sys
import os
import time
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import MagicMock, patch
from core.glasses_client import GlassesClient, MockGlassesClient


# ─── MockGlassesClient ────────────────────────────────────────────────────────

class TestMockGlassesClient:

    def setup_method(self):
        self.client = MockGlassesClient(poll_interval=0.05)

    def teardown_method(self):
        self.client.stop()

    def test_starts_connected(self):
        self.client.start()
        assert self.client.is_connected is True

    def test_sensor_callback_fires(self):
        received = []
        self.client.on_sensor_data = received.append
        self.client.start()
        time.sleep(0.2)
        assert len(received) >= 1

    def test_sensor_data_has_required_keys(self):
        received = []
        self.client.on_sensor_data = received.append
        self.client.start()
        time.sleep(0.2)
        assert received
        data = received[-1]
        for key in ("ambient_temp_c", "ambient_humidity_pct", "light_level",
                    "is_direct_sun", "noise_level_db", "noise_above_threshold", "timestamp"):
            assert key in data, f"Missing key: {key}"

    def test_sensor_values_are_plausible(self):
        received = []
        self.client.on_sensor_data = received.append
        self.client.start()
        time.sleep(0.2)
        data = received[-1]
        assert 0 <= data["ambient_temp_c"] <= 60
        assert 0 <= data["ambient_humidity_pct"] <= 100
        assert isinstance(data["is_direct_sun"], bool)
        assert data["timestamp"] > 0

    def test_scenario_hot_direct_sun(self):
        self.client.start()
        self.client.set_scenario("hot_direct_sun")
        received = []
        self.client.on_sensor_data = received.append
        time.sleep(0.2)
        if received:
            data = received[-1]
            assert data["is_direct_sun"] is True
            assert data["ambient_temp_c"] > 30

    def test_scenario_shade(self):
        self.client.start()
        self.client.set_scenario("shade")
        received = []
        self.client.on_sensor_data = received.append
        time.sleep(0.2)
        if received:
            data = received[-1]
            assert data["is_direct_sun"] is False

    def test_send_display_returns_true(self):
        assert self.client.send_display("orange") is True

    def test_send_display_all_tiers(self):
        for tier in ("green", "yellow", "orange", "red"):
            assert self.client.send_display(tier) is True

    def test_send_display_with_message(self):
        assert self.client.send_display("red", "HR HIGH") is True

    def test_capture_returns_bytes(self):
        result = self.client.capture()
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_capture_returns_jpeg_magic_bytes(self):
        result = self.client.capture()
        assert result[:2] == b"\xff\xd8"  # JPEG SOI marker

    def test_health_check_returns_true(self):
        assert self.client.health_check() is True

    def test_last_sensor_data_updated_after_poll(self):
        self.client.start()
        time.sleep(0.2)
        assert self.client.last_sensor_data is not None

    def test_stop_does_not_raise(self):
        self.client.start()
        time.sleep(0.1)
        self.client.stop()  # should not raise


# ─── GlassesClient (real path with mocked requests) ──────────────────────────

class TestGlassesClientWithMockedRequests:

    def setup_method(self):
        self.client = GlassesClient(
            base_url="http://test-glasses.local:5001",
            poll_interval=0.05,
            timeout=1.0,
        )

    def teardown_method(self):
        self.client.stop()

    def test_get_sensors_parses_json(self):
        fake_data = {
            "ambient_temp_c": 33.5,
            "ambient_humidity_pct": 68.0,
            "light_level": 820,
            "is_direct_sun": True,
            "noise_level_db": 80,
            "noise_above_threshold": False,
            "timestamp": 1713456789.0,
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = fake_data
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp) as mock_get:
            result = self.client._get_sensors()
            mock_get.assert_called_once_with(
                "http://test-glasses.local:5001/sensors", timeout=1.0
            )
            assert result == fake_data

    def test_send_display_posts_correct_payload(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_resp) as mock_post:
            result = self.client.send_display("orange", "HR HIGH")
            assert result is True
            mock_post.assert_called_once_with(
                "http://test-glasses.local:5001/display",
                json={"heat_tier": "orange", "message": "HR HIGH"},
                timeout=1.0,
            )

    def test_send_display_returns_false_on_exception(self):
        with patch("requests.post", side_effect=Exception("connection refused")):
            result = self.client.send_display("red")
            assert result is False

    def test_capture_returns_bytes_from_response(self):
        fake_jpeg = b"\xff\xd8\xff\xe0fake jpeg content\xff\xd9"
        mock_resp = MagicMock()
        mock_resp.content = fake_jpeg
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            result = self.client.capture()
            assert result == fake_jpeg

    def test_capture_returns_none_on_exception(self):
        with patch("requests.get", side_effect=Exception("timeout")):
            result = self.client.capture()
            assert result is None

    def test_health_check_true_on_200(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("requests.get", return_value=mock_resp):
            assert self.client.health_check() is True

    def test_health_check_false_on_exception(self):
        with patch("requests.get", side_effect=Exception("unreachable")):
            assert self.client.health_check() is False

    def test_poll_loop_calls_callback_on_success(self):
        fake_data = {"ambient_temp_c": 30.0, "ambient_humidity_pct": 55.0,
                     "light_level": 700, "is_direct_sun": True,
                     "noise_level_db": 70, "noise_above_threshold": False,
                     "timestamp": time.time()}
        mock_resp = MagicMock()
        mock_resp.json.return_value = fake_data
        mock_resp.raise_for_status = MagicMock()

        received = []
        self.client.on_sensor_data = received.append

        with patch("requests.get", return_value=mock_resp):
            self.client.start()
            time.sleep(0.2)

        assert len(received) >= 1
        assert received[0] == fake_data

    def test_poll_loop_marks_connected_after_success(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ambient_temp_c": 28.0, "ambient_humidity_pct": 60.0,
                                        "light_level": 500, "is_direct_sun": False,
                                        "noise_level_db": 65, "noise_above_threshold": False,
                                        "timestamp": time.time()}
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            self.client.start()
            time.sleep(0.2)
            assert self.client.is_connected is True

    def test_poll_loop_marks_disconnected_after_failure(self):
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                # First 2 calls succeed (marks connected)
                mock_resp = MagicMock()
                mock_resp.json.return_value = {
                    "ambient_temp_c": 28.0, "ambient_humidity_pct": 60.0,
                    "light_level": 500, "is_direct_sun": False,
                    "noise_level_db": 65, "noise_above_threshold": False,
                    "timestamp": time.time()
                }
                mock_resp.raise_for_status = MagicMock()
                return mock_resp
            raise Exception("Pi Zero offline")

        with patch("requests.get", side_effect=side_effect):
            self.client.start()
            time.sleep(0.5)
            assert self.client.is_connected is False

    def test_base_url_trailing_slash_stripped(self):
        client = GlassesClient("http://test.local:5001/")
        assert client.base_url == "http://test.local:5001"

    def test_last_sensor_data_none_before_first_poll(self):
        assert self.client.last_sensor_data is None
