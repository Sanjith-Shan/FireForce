"""
classify.py — Mindrove EMG → Hyperdimensional Classifier
─────────────────────────────────────────────────────────
No torchhd required — HD computing implemented directly with torch.

Pipeline
  1. Load calibration CSVs (Clench, Half_Clench)
  2. Extract features per 1s window (RMS, MAV, ZC, WL per channel)
  3. Train a hyperdimensional centroid model
  4. Stream live EMG, gate on energy, classify with 1s confirmation hold

Spike / general-movement rejection
  • If window RMS > SPIKE_RMS_MULTIPLIER × baseline RMS → label "General"
  • If RMS < ACTIVITY_THRESHOLD → label "Rest"
  • Classification only fires after CONFIRM_SECS of consistent label
"""
from __future__ import annotations

from collections import deque
import time

try:
    import numpy as np
    import pandas as pd
    import torch
    import torch.nn.functional as F
    from pylsl import StreamInlet, resolve_streams
    _DEPS_AVAILABLE = True
except ImportError:
    _DEPS_AVAILABLE = False

# ──────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────
SAMPLE_RATE         = 1000      # Hz (Mindrove default)
WINDOW_SIZE         = SAMPLE_RATE * 1   # 1-second window
WINDOW_STEP         = SAMPLE_RATE // 2  # 50% overlap (500 samples)

HD_DIM              = 10_000    # hypervector dimensionality

# Energy gating — printed on startup, tune after first run
ACTIVITY_THRESHOLD      = 0.02  # RMS below this → Rest (silent)
SPIKE_RMS_MULTIPLIER    = 4.0   # RMS above baseline × this → General

# Temporal confirmation: label must hold this long before firing
CONFIRM_SECS        = 1.0
CONFIRM_WINDOWS     = max(2, int(CONFIRM_SECS / (WINDOW_STEP / SAMPLE_RATE)))

GESTURE_LABELS      = ["Clench", "Half_Clench"]
N_CLASSES           = len(GESTURE_LABELS)

CHANNELS            = None  # inferred from calibration data


# ──────────────────────────────────────────────
# HYPERDIMENSIONAL MODEL (pure torch, no torchhd)
# ──────────────────────────────────────────────
class HDModel:
    """
    Centroid-based hyperdimensional classifier.
    Encoding: random projection → sign binarization
    Inference: cosine similarity to class centroids
    """

    def __init__(self, n_features: int, n_classes: int, dim: int):
        torch.manual_seed(42)
        self.dim       = dim
        self.n_classes = n_classes
        self.proj      = torch.randn(n_features, dim)
        self.centroids = torch.zeros(n_classes, dim)

    def encode(self, x: torch.Tensor) -> torch.Tensor:
        """Project to HD space and binarize. x: (N, features) → (N, dim)"""
        return (x @ self.proj).sign()

    def train(self, X: torch.Tensor, y: torch.Tensor):
        """Accumulate class hypervectors from training windows."""
        for cls in range(self.n_classes):
            mask = y == cls
            if mask.any():
                self.centroids[cls] = self.encode(X[mask]).sum(dim=0)
        self.centroids = F.normalize(self.centroids, dim=1)

    def predict(self, x: torch.Tensor) -> int:
        """Return class index with highest cosine similarity."""
        hv   = self.encode(x.unsqueeze(0))
        sims = self.centroids @ hv.squeeze()
        return int(sims.argmax().item())


# ──────────────────────────────────────────────
# FEATURE EXTRACTION
# ──────────────────────────────────────────────
def extract_features(window: np.ndarray) -> np.ndarray:
    """
    window : (samples, channels)
    Returns 1-D feature vector: [RMS, MAV, ZC, WL] × channels
    """
    rms = np.sqrt(np.mean(window ** 2, axis=0))
    mav = np.mean(np.abs(window), axis=0)
    zc  = np.sum(np.diff(np.sign(window), axis=0) != 0, axis=0) / window.shape[0]
    wl  = np.sum(np.abs(np.diff(window, axis=0)), axis=0) / window.shape[0]
    return np.concatenate([rms, mav, zc, wl]).astype(np.float32)


def window_rms(window: np.ndarray) -> float:
    """Global RMS across all channels — used for energy gating."""
    return float(np.sqrt(np.mean(window ** 2)))


# ──────────────────────────────────────────────
# DATA LOADING
# ──────────────────────────────────────────────
def load_calibration() -> tuple:
    global CHANNELS
    X, y = [], []

    for label_idx, gesture in enumerate(GESTURE_LABELS):
        path = f"calibration_{gesture.lower()}.csv"
        try:
            df = pd.read_csv(path)
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Missing {path} — run calibrate.py first."
            )

        data     = df.values.astype(np.float32)
        CHANNELS = data.shape[1]

        n_windows = 0
        for start in range(0, len(data) - WINDOW_SIZE, WINDOW_STEP):
            w = data[start : start + WINDOW_SIZE]
            X.append(extract_features(w))
            y.append(label_idx)
            n_windows += 1

        print(f"  {gesture}: {data.shape[0]} samples → {n_windows} windows")

    return np.array(X, dtype=np.float32), np.array(y, dtype=np.int64)


# ──────────────────────────────────────────────
# LIVE CLASSIFICATION
# ──────────────────────────────────────────────
def stream_classify(inlet: StreamInlet, model: HDModel, baseline_rms: float):
    buffer        = deque(maxlen=WINDOW_SIZE)
    step_counter  = 0
    label_history = deque(maxlen=CONFIRM_WINDOWS)
    last_fired    = ""

    spike_threshold = baseline_rms * SPIKE_RMS_MULTIPLIER

    print(f"  Baseline RMS : {baseline_rms:.4f}")
    print(f"  Spike gate   : > {spike_threshold:.4f} → General")
    print(f"  Rest gate    : < {ACTIVITY_THRESHOLD:.4f} → Rest (silent)")
    print(f"  Confirm hold : {CONFIRM_SECS}s ({CONFIRM_WINDOWS} windows)\n")
    print("Streaming — press Ctrl+C to stop.\n")

    while True:
        # timeout=0.1 keeps pull_sample non-blocking so Ctrl+C works
        sample, _ = inlet.pull_sample(timeout=0.1)
        if not sample:
            continue

        buffer.append(sample)
        step_counter += 1

        if len(buffer) < WINDOW_SIZE or step_counter % WINDOW_STEP != 0:
            continue

        window = np.array(buffer, dtype=np.float32)
        rms    = window_rms(window)

        # ── Energy gates ──────────────────────────────────────
        if rms < ACTIVITY_THRESHOLD:
            label_history.clear()
            last_fired = ""
            continue

        if rms > spike_threshold:
            label = "General"
        else:
            feat  = extract_features(window)
            idx   = model.predict(torch.tensor(feat))
            label = GESTURE_LABELS[idx]

        # ── Temporal confirmation ──────────────────────────────
        label_history.append(label)

        all_same    = len(set(label_history)) == 1
        buffer_full = len(label_history) == CONFIRM_WINDOWS
        new_label   = label != last_fired

        if buffer_full and all_same and new_label:
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}]  ▶  {label.upper()}   (rms={rms:.4f})")
            last_fired = label
        elif not all_same:
            last_fired = ""


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────
def main():
    print("\nMindrove HD Classifier")
    print("─" * 40)

    print("\n[1/3] Loading calibration data...")
    X, y = load_calibration()

    print("\n[2/3] Training HD model...")
    n_features = X.shape[1]
    model = HDModel(n_features=n_features, n_classes=N_CLASSES, dim=HD_DIM)

    X_t = torch.tensor(X)
    y_t = torch.tensor(y)

    print(f"  Encoding {len(X)} windows into {HD_DIM}-D hypervectors...")
    model.train(X_t, y_t)
    print("  Done.\n")

    baseline_rms = float(np.sqrt(np.mean(X[:, :CHANNELS] ** 2)))

    print("[3/3] Connecting to LSL stream...")
    streams = resolve_streams()
    if not streams:
        print("ERROR: No LSL stream found. Launch Mindrove Connect and enable LSL.")
        return

    inlet = StreamInlet(streams[0])
    print("Connected.\n")

    try:
        stream_classify(inlet, model, baseline_rms)
    except KeyboardInterrupt:
        print("\nClassifier stopped.")


if __name__ == "__main__":
    main()