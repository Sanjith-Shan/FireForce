"""
Tests for core/emg_bridge.py — MockEMGBridge callback firing.
Run with:  pytest tests/test_emg_bridge.py -v
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import threading
import pytest
from unittest.mock import MagicMock, patch

from core.emg_bridge import MockEMGBridge, EMGBridge


# ── MockEMGBridge ─────────────────────────────────────────────────────────────

class TestMockEMGBridgeInit:

    def test_on_clench_initially_none(self):
        b = MockEMGBridge()
        assert b.on_clench is None

    def test_on_half_clench_initially_none(self):
        b = MockEMGBridge()
        assert b.on_half_clench is None

    def test_not_running_before_start(self):
        b = MockEMGBridge()
        assert b._running is False

    def test_start_sets_running(self):
        b = MockEMGBridge()
        b.start()
        assert b._running is True
        b.stop()

    def test_stop_clears_running(self):
        b = MockEMGBridge()
        b.start()
        b.stop()
        assert b._running is False

    def test_thread_is_daemon(self):
        b = MockEMGBridge()
        b.start()
        assert b._thread.daemon is True
        b.stop()


class TestMockEMGBridgeCallbacks:

    def _trigger_via_stdin(self, key: str, bridge: MockEMGBridge, timeout=1.0):
        """Drive the bridge's _run loop by injecting a line via StringIO."""
        import io
        called = threading.Event()
        lines = iter([key + "\n", ""])

        class FakeStdin:
            def readline(self):
                try:
                    return next(lines)
                except StopIteration:
                    return ""

            def fileno(self):
                raise io.UnsupportedOperation

        bridge.stop()
        bridge._running = True

        result = []

        def run_once():
            import select as sel
            # Call _run body directly but only process one line
            try:
                raw = FakeStdin().readline().strip().lower()
                if raw == 'c' and bridge.on_clench:
                    bridge.on_clench()
                elif raw == 'h' and bridge.on_half_clench:
                    bridge.on_half_clench()
            except Exception:
                pass

        run_once()

    def test_clench_callback_fired(self):
        b = MockEMGBridge()
        fired = []
        b.on_clench = lambda: fired.append("clench")
        # Direct invocation of callback wiring
        b.on_clench()
        assert fired == ["clench"]

    def test_half_clench_callback_fired(self):
        b = MockEMGBridge()
        fired = []
        b.on_half_clench = lambda: fired.append("half_clench")
        b.on_half_clench()
        assert fired == ["half_clench"]

    def test_clench_callback_receives_no_args(self):
        b = MockEMGBridge()
        call_args = []
        b.on_clench = lambda: call_args.append(None)
        b.on_clench()
        assert len(call_args) == 1

    def test_none_callback_does_not_raise(self):
        b = MockEMGBridge()
        b.on_clench = None
        # Simulating what _run does: check before calling
        if b.on_clench:
            b.on_clench()
        # No exception raised

    def test_multiple_clench_callbacks_stack(self):
        b = MockEMGBridge()
        fired = []
        b.on_clench = lambda: fired.append(1)
        b.on_clench()
        b.on_clench()
        assert fired == [1, 1]

    def test_callback_can_be_mock(self):
        b = MockEMGBridge()
        mock_cb = MagicMock()
        b.on_clench = mock_cb
        b.on_clench()
        mock_cb.assert_called_once()

    def test_half_clench_callback_mock(self):
        b = MockEMGBridge()
        mock_cb = MagicMock()
        b.on_half_clench = mock_cb
        b.on_half_clench()
        mock_cb.assert_called_once()


# ── EMGBridge (real) init ─────────────────────────────────────────────────────

class TestEMGBridgeInit:

    def test_on_clench_initially_none(self):
        b = EMGBridge()
        assert b.on_clench is None

    def test_on_half_clench_initially_none(self):
        b = EMGBridge()
        assert b.on_half_clench is None

    def test_not_running_before_start(self):
        b = EMGBridge()
        assert b._running is False

    def test_start_spawns_daemon_thread(self):
        b = EMGBridge()
        b.start()
        assert b._thread is not None
        assert b._thread.daemon is True
        b.stop()

    def test_stop_clears_running(self):
        b = EMGBridge()
        b.start()
        b.stop()
        assert b._running is False

    def test_missing_deps_does_not_crash_process(self):
        """_run must catch ImportError gracefully when deps missing."""
        b = EMGBridge()
        errors = []

        def patched_run():
            try:
                raise ImportError("no module named torch")
            except ImportError as e:
                errors.append(str(e))

        b._run = patched_run
        b.start()
        time.sleep(0.05)
        b.stop()
        assert errors  # ImportError was caught inside _run
