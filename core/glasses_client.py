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

    def _get_sensors(self) -> Optional[dict]:
        if not REQUESTS_AVAILABLE:
            return None
        resp = requests.get(f"{self.base_url}/sensors", timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def send_display(self, heat_tier: str, message: str = "") -> bool:
        if not REQUESTS_AVAILABLE:
            logger.debug(f"[mock] display → tier={heat_tier} msg={message!r}")
            return True
        try:
            resp = requests.post(
                f"{self.base_url}/display",
                json={"heat_tier": heat_tier, "message": message},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.warning(f"Failed to update display: {e}")
            return False

    def capture(self) -> Optional[bytes]:
        if not REQUESTS_AVAILABLE:
            return None
        try:
            resp = requests.get(f"{self.base_url}/capture", timeout=10.0)
            resp.raise_for_status()
            return resp.content
        except Exception as e:
            logger.warning(f"Capture failed: {e}")
            return None

    def health_check(self) -> bool:
        if not REQUESTS_AVAILABLE:
            return False
        try:
            resp = requests.get(f"{self.base_url}/health", timeout=self.timeout)
            return resp.status_code == 200
        except Exception:
            return False


class MockGlassesClient(GlassesClient):
    """Drop-in simulator for laptop testing — no network needed."""

    def __init__(self, poll_interval: float = 2.0):
        super().__init__(base_url="http://mock-glasses.local:5001", poll_interval=poll_interval)
        self._scenario = "normal"

    def start(self):
        self._running = True
        self._connected = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("Mock glasses client started")

    def _get_sensors(self) -> dict:
        import random
        if self._scenario == "hot_direct_sun":
            temp = 36.0 + random.uniform(-0.5, 0.5)
            humidity = 70.0 + random.uniform(-2, 2)
            light = 900 + random.randint(-20, 20)
        elif self._scenario == "shade":
            temp = 30.0 + random.uniform(-0.5, 0.5)
            humidity = 55.0 + random.uniform(-2, 2)
            light = 300 + random.randint(-20, 20)
        else:
            temp = 28.0 + random.uniform(-1, 1)
            humidity = 60.0 + random.uniform(-3, 3)
            light = 650 + random.randint(-30, 30)
        return {
            "ambient_temp_c": round(temp, 1),
            "ambient_humidity_pct": round(humidity, 1),
            "light_level": light,
            "is_direct_sun": light > 700,
            "noise_level_db": 72 + random.randint(-5, 5),
            "noise_above_threshold": False,
            "timestamp": time.time(),
        }

    def send_display(self, heat_tier: str, message: str = "") -> bool:
        logger.info(f"[mock glasses display] tier={heat_tier} msg={message!r}")
        return True

    def capture(self) -> Optional[bytes]:
        logger.info("[mock glasses capture]")
        return None

    def health_check(self) -> bool:
        return True

    def set_scenario(self, scenario: str):
        """Scenarios: 'normal', 'hot_direct_sun', 'shade'"""
        self._scenario = scenario
        logger.info(f"Mock glasses scenario → {scenario}")
