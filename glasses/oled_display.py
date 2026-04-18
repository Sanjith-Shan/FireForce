"""
SolSpecs — OLED Display
Renders heat tier color dots and short status messages on the SSD1306/SH1106
128×64 monochrome OLED using the luma.oled library.

Colors are encoded as fill patterns since the display is monochrome:
    green  → solid circle, no blink
    yellow → solid circle, slow blink (1 Hz)
    orange → solid circle + ring, slow blink (1 Hz)
    red    → solid circle + ring, fast blink (4 Hz)
"""

import logging
import threading
import time

logger = logging.getLogger("OLEDDisplay")

try:
    from luma.core.interface.serial import i2c, spi
    from luma.oled.device import ssd1306, sh1106
    from luma.core.render import canvas
    from PIL import ImageFont
    LUMA_AVAILABLE = True
except ImportError:
    LUMA_AVAILABLE = False
    logger.warning("luma.oled not installed. Run: pip install luma.oled")


# Blink timing per tier
_BLINK_INTERVAL = {
    "green": None,     # no blink
    "yellow": 1.0,     # 1 Hz
    "orange": 1.0,     # 1 Hz
    "red": 0.25,       # 4 Hz
}

_TIER_PATTERNS = {
    "green":  {"fill": "white", "ring": False},
    "yellow": {"fill": "white", "ring": False},
    "orange": {"fill": "white", "ring": True},
    "red":    {"fill": "white", "ring": True},
}


class OLEDDisplay:
    """
    Manages the 128×64 OLED. Handles blink animation in a background thread.

    On UNO Q / Pi Zero hardware the display is connected via I2C (address 0x3C).
    Swap to spi() if using SPI wiring.
    """

    WIDTH = 128
    HEIGHT = 64
    CIRCLE_RADIUS = 20
    CIRCLE_CENTER = (32, 32)  # left half of screen

    def __init__(self, i2c_port: int = 1, i2c_address: int = 0x3C):
        self._device = None
        self._current_tier = "green"
        self._current_message = ""
        self._blink_state = True
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        if LUMA_AVAILABLE:
            try:
                serial = i2c(port=i2c_port, address=i2c_address)
                self._device = ssd1306(serial)
                logger.info(f"OLED connected at I2C {i2c_address:#x}")
            except Exception as e:
                logger.warning(f"OLED hardware init failed: {e}")
        else:
            logger.info("OLED running in log-only mode (luma.oled not available)")

        self._start_blink_thread()

    def show_tier(self, heat_tier: str, message: str = ""):
        """Update the display for a new heat tier."""
        with self._lock:
            self._current_tier = heat_tier
            self._current_message = message
            self._blink_state = True  # reset blink phase on tier change
        self._render()

    def _start_blink_thread(self):
        self._thread = threading.Thread(target=self._blink_loop, daemon=True)
        self._thread.start()

    def _blink_loop(self):
        while not self._stop_event.is_set():
            with self._lock:
                tier = self._current_tier
                interval = _BLINK_INTERVAL.get(tier)

            if interval:
                with self._lock:
                    self._blink_state = not self._blink_state
                self._render()
                time.sleep(interval / 2)
            else:
                time.sleep(0.1)

    def _render(self):
        with self._lock:
            tier = self._current_tier
            message = self._current_message
            visible = self._blink_state

        pattern = _TIER_PATTERNS.get(tier, _TIER_PATTERNS["green"])
        cx, cy = self.CIRCLE_CENTER
        r = self.CIRCLE_RADIUS

        if self._device and LUMA_AVAILABLE:
            try:
                with canvas(self._device) as draw:
                    if visible:
                        # Filled circle
                        draw.ellipse(
                            [cx - r, cy - r, cx + r, cy + r],
                            fill=pattern["fill"],
                            outline="white",
                        )
                        # Outer ring for orange/red
                        if pattern["ring"]:
                            draw.ellipse(
                                [cx - r - 4, cy - r - 4, cx + r + 4, cy + r + 4],
                                fill=None,
                                outline="white",
                            )
                        # Message text on right half
                        if message:
                            draw.text((70, 20), tier.upper(), fill="white")
                            draw.text((70, 36), message[:10], fill="white")
                        else:
                            draw.text((70, 24), tier.upper(), fill="white")
            except Exception as e:
                logger.debug(f"Render error: {e}")
        else:
            if visible:
                logger.debug(f"[OLED] {tier.upper()} {'*' if pattern['ring'] else 'o'} {message!r}")

    def clear(self):
        """Blank the display."""
        if self._device and LUMA_AVAILABLE:
            try:
                with canvas(self._device) as draw:
                    draw.rectangle(
                        [0, 0, self.WIDTH - 1, self.HEIGHT - 1], fill="black"
                    )
            except Exception as e:
                logger.debug(f"Clear error: {e}")

    def stop(self):
        self._stop_event.set()
        self.clear()
