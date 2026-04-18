"""
BlindGuide — EMG Gesture Classifier
Classifies forearm EMG signals into gestures: rest, describe, converse.

Two modes:
    1. Threshold mode (simple, reliable): envelope > threshold = gesture
    2. ML mode (if you have time): collect data, train LDA/SVM, classify

Can test the ML pipeline right now with synthetic data.
When hardware arrives, swap synthetic data for real ADC readings.
"""

import numpy as np
import time
import logging
from collections import deque
from typing import Optional

logger = logging.getLogger("EMGClassifier")


class EMGFeatureExtractor:
    """Extract features from raw EMG signal windows."""

    def __init__(self, window_size: int = 200, sample_rate: int = 500):
        self.window_size = window_size
        self.sample_rate = sample_rate

    def extract(self, window: np.ndarray) -> np.ndarray:
        """
        Extract standard EMG features from a signal window.
        Returns feature vector used for classification.
        """
        if len(window) < 10:
            return np.zeros(6)

        # 1. Mean Absolute Value (MAV) — classic EMG amplitude measure
        mav = np.mean(np.abs(window))

        # 2. Root Mean Square (RMS) — power of the signal
        rms = np.sqrt(np.mean(window ** 2))

        # 3. Waveform Length (WL) — complexity of signal
        wl = np.sum(np.abs(np.diff(window)))

        # 4. Zero Crossing Rate (ZCR) — frequency content proxy
        zcr = np.sum(np.diff(np.sign(window)) != 0) / len(window)

        # 5. Slope Sign Changes (SSC) — another frequency proxy
        diff = np.diff(window)
        ssc = np.sum(np.diff(np.sign(diff)) != 0) / len(diff) if len(diff) > 1 else 0

        # 6. Variance
        var = np.var(window)

        return np.array([mav, rms, wl, zcr, ssc, var])


class ThresholdClassifier:
    """
    Simple threshold-based gesture detection.
    Most reliable for hackathon — 2 gestures + rest.
    
    Gesture 1 (describe): short strong flex (wrist extension)
    Gesture 2 (converse): sustained flex (fist clench held 1+ sec)
    """

    def __init__(self):
        self.flex_threshold = 300      # ADC value, calibrate at hackathon
        self.sustain_threshold_ms = 800  # how long to hold for "converse"
        self.cooldown_ms = 2000         # min time between gestures

        self._flex_start = 0
        self._last_gesture_time = 0
        self._above_threshold = False

    def update(self, emg_envelope: float) -> Optional[str]:
        """
        Call this at ~50-100Hz with the rectified+smoothed EMG envelope.
        Returns "describe", "converse", or None.
        """
        now = time.time() * 1000  # ms

        # Cooldown check
        if now - self._last_gesture_time < self.cooldown_ms:
            return None

        if emg_envelope > self.flex_threshold:
            if not self._above_threshold:
                self._above_threshold = True
                self._flex_start = now
        else:
            if self._above_threshold:
                self._above_threshold = False
                duration = now - self._flex_start

                if duration > self.sustain_threshold_ms:
                    self._last_gesture_time = now
                    logger.info(f"Gesture: CONVERSE (held {duration:.0f}ms)")
                    return "converse"
                elif duration > 100:  # ignore very brief spikes (noise)
                    self._last_gesture_time = now
                    logger.info(f"Gesture: DESCRIBE (flex {duration:.0f}ms)")
                    return "describe"

        return None

    def calibrate(self, rest_values: list, flex_values: list):
        """Set threshold based on collected rest and flex data."""
        rest_max = np.percentile(rest_values, 95)
        flex_min = np.percentile(flex_values, 5)
        self.flex_threshold = (rest_max + flex_min) / 2
        logger.info(f"Calibrated threshold: {self.flex_threshold:.0f} "
                    f"(rest 95th: {rest_max:.0f}, flex 5th: {flex_min:.0f})")


class MLClassifier:
    """
    ML-based gesture classifier using scikit-learn.
    Train on collected data, then classify in real-time.
    """

    def __init__(self):
        self.feature_extractor = EMGFeatureExtractor()
        self.model = None
        self.scaler = None
        self.labels = ["rest", "describe", "converse"]
        self.is_trained = False

    def train(self, data: dict[str, list[np.ndarray]]):
        """
        Train the classifier on labeled windows.
        
        Args:
            data: {"rest": [window1, window2, ...], "describe": [...], "converse": [...]}
        """
        from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
        from sklearn.preprocessing import StandardScaler
        from sklearn.model_selection import cross_val_score

        X, y = [], []
        for label, windows in data.items():
            for window in windows:
                features = self.feature_extractor.extract(window)
                X.append(features)
                y.append(label)

        X = np.array(X)
        y = np.array(y)

        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)

        self.model = LinearDiscriminantAnalysis()
        
        # Cross-validation
        scores = cross_val_score(self.model, X_scaled, y, cv=5)
        logger.info(f"EMG classifier CV accuracy: {scores.mean():.2f} ± {scores.std():.2f}")

        # Train on all data
        self.model.fit(X_scaled, y)
        self.is_trained = True
        logger.info("EMG classifier trained")

    def predict(self, window: np.ndarray) -> tuple[str, float]:
        """
        Classify a signal window.
        Returns (gesture_name, confidence).
        """
        if not self.is_trained:
            return "rest", 0.0

        features = self.feature_extractor.extract(window).reshape(1, -1)
        features_scaled = self.scaler.transform(features)

        prediction = self.model.predict(features_scaled)[0]
        probabilities = self.model.predict_proba(features_scaled)[0]
        confidence = max(probabilities)

        return prediction, confidence


class EMGProcessor:
    """
    Real-time EMG processing pipeline.
    Buffers raw ADC samples, extracts envelope, classifies gestures.
    """

    def __init__(self, use_ml: bool = False):
        self.buffer = deque(maxlen=500)  # ~1 second at 500Hz
        self.envelope_buffer = deque(maxlen=50)  # smoothed envelope
        self.threshold_classifier = ThresholdClassifier()
        self.ml_classifier = MLClassifier() if use_ml else None
        self.use_ml = use_ml

        # Envelope filter params
        self.envelope_alpha = 0.1  # low-pass filter coefficient

        self._envelope = 0.0

    def add_sample(self, raw_adc: int) -> Optional[str]:
        """
        Feed a single ADC sample. Call at your sample rate.
        Returns gesture name if detected, else None.
        """
        # Remove DC offset (simple high-pass: subtract running mean)
        self.buffer.append(raw_adc)
        if len(self.buffer) < 10:
            return None

        mean = np.mean(list(self.buffer)[-50:]) if len(self.buffer) >= 50 else np.mean(self.buffer)
        sample = raw_adc - mean

        # Rectify + exponential moving average → envelope
        rectified = abs(sample)
        self._envelope = self.envelope_alpha * rectified + (1 - self.envelope_alpha) * self._envelope
        self.envelope_buffer.append(self._envelope)

        # Classify using threshold method
        gesture = self.threshold_classifier.update(self._envelope)

        # Optionally also check ML classifier
        if self.use_ml and self.ml_classifier.is_trained and len(self.buffer) >= 200:
            window = np.array(list(self.buffer)[-200:])
            ml_gesture, confidence = self.ml_classifier.predict(window)
            if ml_gesture != "rest" and confidence > 0.85:
                gesture = ml_gesture

        return gesture

    @property
    def current_envelope(self) -> float:
        return self._envelope


def generate_synthetic_emg(gesture: str, n_samples: int = 500, noise_level: float = 50) -> np.ndarray:
    """Generate fake EMG data for testing the pipeline without hardware."""
    t = np.arange(n_samples) / 500.0  # 500Hz

    if gesture == "rest":
        signal = np.random.randn(n_samples) * noise_level * 0.3

    elif gesture == "describe":
        # Short burst of activity
        signal = np.random.randn(n_samples) * noise_level * 0.3
        burst_start = n_samples // 3
        burst_end = burst_start + n_samples // 4
        signal[burst_start:burst_end] += np.random.randn(burst_end - burst_start) * noise_level * 3

    elif gesture == "converse":
        # Sustained contraction
        signal = np.random.randn(n_samples) * noise_level * 0.3
        burst_start = n_samples // 4
        burst_end = burst_start + n_samples // 2
        signal[burst_start:burst_end] += np.random.randn(burst_end - burst_start) * noise_level * 2.5

    else:
        signal = np.random.randn(n_samples) * noise_level

    return signal


# ─── Test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== Test 1: Feature extraction ===")
    fe = EMGFeatureExtractor()
    rest_signal = generate_synthetic_emg("rest")
    flex_signal = generate_synthetic_emg("describe")
    rest_feat = fe.extract(rest_signal)
    flex_feat = fe.extract(flex_signal)
    print(f"Rest features:  MAV={rest_feat[0]:.1f}, RMS={rest_feat[1]:.1f}")
    print(f"Flex features:  MAV={flex_feat[0]:.1f}, RMS={flex_feat[1]:.1f}")
    print(f"MAV ratio (flex/rest): {flex_feat[0]/rest_feat[0]:.1f}x")

    print("\n=== Test 2: ML classifier training ===")
    try:
        from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
        ml = MLClassifier()
        training_data = {
            "rest": [generate_synthetic_emg("rest") for _ in range(30)],
            "describe": [generate_synthetic_emg("describe") for _ in range(30)],
            "converse": [generate_synthetic_emg("converse") for _ in range(30)],
        }
        ml.train(training_data)

        # Test predictions
        for gesture in ["rest", "describe", "converse"]:
            test_signal = generate_synthetic_emg(gesture)
            pred, conf = ml.predict(test_signal)
            status = "✓" if pred == gesture else "✗"
            print(f"  {status} True: {gesture:10s} → Predicted: {pred:10s} (conf={conf:.2f})")

    except ImportError:
        print("scikit-learn not installed. Run: pip install scikit-learn")

    print("\n=== Test 3: Real-time threshold classifier ===")
    processor = EMGProcessor(use_ml=False)
    processor.threshold_classifier.flex_threshold = 80  # lower for synthetic

    # Simulate feeding samples from a "describe" gesture
    signal = generate_synthetic_emg("describe", n_samples=1000)
    detected = []
    for sample in signal:
        result = processor.add_sample(int(sample + 512))  # center around 512
        if result:
            detected.append(result)

    print(f"  Detected gestures: {detected}")
    print("\nAll EMG tests complete.")
