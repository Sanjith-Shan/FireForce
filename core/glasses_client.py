"""
SolSpecs — Glasses Client
HTTP client running on the UNO Q that talks to the Pi Zero Flask server.

Polls /sensors every 2 seconds, requests /capture on demand,
sends /display when heat tier changes. Handles connection drops gracefully.
"""

import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger("GlassesClient")

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    logger.warning("requests not installed. Run: pip install requests")


class GlassesClient:
    """
    Polls the Pi Zero sensor server and sends display commands.

    Usage:
        client = GlassesClient("http://solspecs-glasses.local:5001")
        client.on_sensor_data = lambda data: print(data)
        client.start()
        ...
        client.send_display("orange")
        image_bytes = client.capture()
        client.stop()
    """

    def __init__(self, base_url: str, poll_interval: float = 2.0, timeout: float = 3.0):
        self.base_url = base_url.rstrip("/")
        self.poll_interval = poll_interval
        self.timeout = timeout

        self.on_sensor_data: Optional[Callable[[dict], None]] = None

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._connected = False
        self._last_sensor_data: Optional[dict] = None
        self._consecutive_failures = 0
        self._max_failures_before_warn = 3

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info(f"Glasses client started — polling {self.base_url}")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Glasses client stopped")

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def last_sensor_data(self) -> Optional[dict]:
        return self._last_sensor_data

    # ── Internal poll loop ────────────────────────────────────────────

    def _poll_loop(self):
        while self._running:
            try:
                data = self._get_sensors()
                if data:
                    self._last_sensor_data = data
                    self._consecutive_failures = 0
                    if not self._connected:
                        logger.info("Glasses connected")
                        self._connected = True
                    if self.on_sensor_data:
                        self.on_sensor_data(data)
            except Exception as e:
                self._handle_failure(e)
            time.sleep(self.poll_interval)

    def _handle_failure(self, exc: Exception):
        self._consecutive_failures += 1
        if self._connected:
            logger.warning(f"Glasses connection lost: {exc}")
            self._connected = False
        elif self._consecutive_failures == self._max_failures_before_warn:
            logger.warning(f"Glasses unreachable after {self._consecutive_failures} attempts ({exc})")

    # ── Public API ────────────────────────────────────────────────────

    def _get_sensors(self) -> Optional[dict]:
        """Fetch sensor data from Pi Zero. Returns dict or None."""
        if not REQUESTS_AVAILABLE:
            return None
        resp = requests.get(f"{self.base_url}/sensors", timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def send_display(self, heat_tier: str, message: str = "") -> bool:
        """
        Push heat tier and optional short message to the OLED.

        Args:
            heat_tier: "green" | "yellow" | "orange" | "red"
            message:   Up to ~10 chars shown below the color dot.

        Returns:
            True if acknowledged, False on failure.
        """
        if not REQUESTS_AVAILABLE:
            logger.debug(f"[mock] display → tier={heat_tier} msg={message!r}")
            return True
        try:
            payload = {"heat_tier": heat_tier, "message": message}
            resp = requests.post(
                f"{self.base_url}/display",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.warning(f"Failed to update display: {e}")
            return False

    def capture(self) -> Optional[bytes]:
        """
        Request a JPEG frame from the Pi Camera.

        Returns:
            Raw JPEG bytes, or None on failure.
        """
        if not REQUESTS_AVAILABLE:
            logger.debug("[mock] capture requested")
            return None
        try:
            resp = requests.get(f"{self.base_url}/capture", timeout=10.0)
            resp.raise_for_status()
            return resp.content
        except Exception as e:
            logger.warning(f"Capture failed: {e}")
            return None

    def health_check(self) -> bool:
        """Returns True if the Pi Zero is reachable."""
        if not REQUESTS_AVAILABLE:
            return False
        try:
            resp = requests.get(f"{self.base_url}/health", timeout=self.timeout)
            return resp.status_code == 200
        except Exception:
            return False


class MockGlassesClient(GlassesClient):
    """
    Drop-in simulator for laptop testing — no network needed.
    Generates realistic fake sensor data and logs display/capture calls.
    """

    import random as _random

    def __init__(self, poll_interval: float = 2.0):
        super().__init__(base_url="http://mock-glasses.local:5001", poll_interval=poll_interval)
        self._scenario = "normal"
        self._sun_minutes = 0.0

    def start(self):
        self._running = True
        self._connected = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("Mock glasses client started")

    def _get_sensors(self) -> dict:
        import random
        self._sun_minutes += self.poll_interval / 60.0

        if self._scenario == "hot_direct_sun":
            temp = 36.0 + random.uniform(-0.5, 0.5)
            humidity = 70.0 + random.uniform(-2, 2)
            light = 900 + random.randint(-20, 20)
            is_sun = True
        elif self._scenario == "shade":
            temp = 30.0 + random.uniform(-0.5, 0.5)
            humidity = 55.0 + random.uniform(-2, 2)
            light = 300 + random.randint(-20, 20)
            is_sun = False
        else:  # normal
            temp = 28.0 + random.uniform(-1, 1)
            humidity = 60.0 + random.uniform(-3, 3)
            light = 650 + random.randint(-30, 30)
            is_sun = light > 700

        return {
            "ambient_temp_c": round(temp, 1),
            "ambient_humidity_pct": round(humidity, 1),
            "light_level": light,
            "is_direct_sun": is_sun,
            "noise_level_db": 72 + random.randint(-5, 5),
            "noise_above_threshold": False,
            "timestamp": time.time(),
        }

    def send_display(self, heat_tier: str, message: str = "") -> bool:
        logger.info(f"[mock glasses display] tier={heat_tier} msg={message!r}")
        return True

    def capture(self) -> Optional[bytes]:
        logger.info("[mock glasses capture] returning test JPEG placeholder")
        # Minimal valid 1×1 white JPEG
        return (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
            b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
            b"\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\x1e"
            b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
            b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00"
            b"\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
            b"\xff\xc4\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04"
            b"\x04\x00\x00\x01}\x01\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa"
            b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xfb\xd2\x8a(\x03\xff\xd9"
        )

    def health_check(self) -> bool:
        return True

    def set_scenario(self, scenario: str):
        """Scenarios: 'normal', 'hot_direct_sun', 'shade'"""
        self._scenario = scenario
        logger.info(f"Mock glasses scenario → {scenario}")
