"""
calibrate.py — Mindrove EMG Calibration
Collects labeled data for: Clench, Half_Clench
General movement is handled implicitly at classification time via energy thresholding.
"""

import numpy as np
import pandas as pd
from pylsl import StreamInlet, resolve_streams
import time

# ──────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────
REST_SECS = 4  # mandatory rest between gestures

# Per-gesture settings: (target_samples, countdown_secs)
# Half_Clench uses a shorter countdown (1s) so the hand is already
# in position when recording starts, and 8s to reduce fatigue.
GESTURE_CONFIG = {
    "Clench":      {"samples": 10_000, "countdown": 3},
    "Half_Clench": {"samples":  8_000, "countdown": 1},
}
GESTURES = list(GESTURE_CONFIG.keys())


# ──────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────
def countdown(seconds: int):
    for i in range(seconds, 0, -1):
        print(f"  Starting in {i}...", end="\r")
        time.sleep(1)
    print()


def collect_gesture(inlet: StreamInlet, gesture: str) -> pd.DataFrame:
    cfg    = GESTURE_CONFIG[gesture]
    target = cfg["samples"]
    print(f"\n{'='*52}")
    print(f"  GESTURE: {gesture.upper()}")
    print(f"  Hold a steady {gesture} for ~{target // 1000}s once recording starts.")
    if gesture == "Half_Clench":
        print("  Get your hand into position NOW before countdown ends.")
    print(f"{'='*52}")
    countdown(cfg["countdown"])

    # Flush any samples that queued up during rest/countdown so we start at 0%
    # (otherwise the second gesture begins with thousands of stale samples).
    flushed = 0
    while True:
        s, _ = inlet.pull_sample(timeout=0.0)
        if s is None:
            break
        flushed += 1
    if flushed:
        print(f"  (flushed {flushed} stale samples)")

    print("  >>> RECORDING — HOLD POSITION <<<")

    data = []
    last_pct_shown = -1
    while len(data) < target:
        sample, _ = inlet.pull_sample()
        if sample:
            data.append(sample)
            pct = int(100 * len(data) / target)
            if pct != last_pct_shown and pct % 5 == 0:
                bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
                print(f"  [{bar}] {pct:5.1f}%", end="\r")
                last_pct_shown = pct

    print(f"\n  ✓ Captured {len(data)} samples.")
    cols = [f"CH_{i+1}" for i in range(len(data[0]))]
    return pd.DataFrame(data, columns=cols)


def save(df: pd.DataFrame, gesture: str):
    path = f"data/calibration_{gesture.lower()}.csv"
    df.to_csv(path, index=False)
    print(f"  Saved → {path}")


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────
def main():
    print("\nMindrove EMG Calibration")
        print("\n✓ All calibration files ready. Run classify.py to train and stream.\n")

    except KeyboardInterrupt:
        print("\nCalibration interrupted by user.")


if __name__ == "__main__":
    main()