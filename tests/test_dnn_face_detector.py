"""Guard the vendored DNN face detector and its box-conversion math.

Mirrors test_haar_cascades.py's guard for the Haar build: this touches the
real model files, since nothing else in the suite constructs the net.
"""

from __future__ import annotations

import numpy as np
import pytest

from apps.pipeline.scoring import DnnFaceDetector, ScoringError, dnn_face_detector


class _FakeNet:
    """Stands in for cv2.dnn.Net — forward() returns a canned SSD-shaped
    detections array (1, 1, N, 7): [batch, class, confidence, x1, y1, x2, y2],
    box coordinates normalized to [0, 1]."""

    def __init__(self, detections: np.ndarray) -> None:
        self._detections = detections

    def setInput(self, blob) -> None:
        pass

    def forward(self) -> np.ndarray:
        return self._detections


def _detections(*rows: tuple[float, float, float, float, float]) -> np.ndarray:
    """Each row is (confidence, x1, y1, x2, y2)."""
    full_rows = [[0, 1, confidence, x1, y1, x2, y2] for confidence, x1, y1, x2, y2 in rows]
    return np.array([[full_rows]], dtype=np.float32)


def test_dnn_face_detector_filters_by_confidence_and_scales_boxes():
    net = _FakeNet(
        _detections(
            (0.9, 0.1, 0.2, 0.3, 0.4),  # above threshold
            (0.2, 0.5, 0.5, 0.6, 0.6),  # below threshold
        )
    )
    detector = DnnFaceDetector(net=net, confidence_threshold=0.5)
    frame = np.zeros((200, 100, 3), dtype=np.uint8)  # height=200, width=100

    boxes = detector.detectMultiScale(frame)

    assert len(boxes) == 1
    x, y, w, h = boxes[0]
    assert (x, y, w, h) == (10, 40, 20, 40)


def test_dnn_face_detector_accepts_grayscale_frames():
    net = _FakeNet(_detections((0.9, 0.0, 0.0, 0.5, 0.5)))
    detector = DnnFaceDetector(net=net, confidence_threshold=0.5)
    frame = np.zeros((200, 100), dtype=np.uint8)  # 2D, like scoring's frames

    boxes = detector.detectMultiScale(frame)

    assert len(boxes) == 1


def test_dnn_face_detector_drops_degenerate_boxes():
    # A box entirely outside the frame clamps to zero width/height.
    net = _FakeNet(_detections((0.9, 1.5, 1.5, 1.6, 1.6)))
    detector = DnnFaceDetector(net=net, confidence_threshold=0.5)
    frame = np.zeros((200, 100, 3), dtype=np.uint8)

    boxes = detector.detectMultiScale(frame)

    assert boxes == []


def test_dnn_face_net_loads():
    detector = dnn_face_detector(confidence_threshold=0.5)
    frame = np.random.default_rng(0).integers(0, 255, size=(300, 300, 3), dtype=np.uint8)

    boxes = detector.detectMultiScale(frame)

    assert isinstance(boxes, list)


def test_dnn_face_net_missing_weights_raises(monkeypatch, tmp_path):
    from apps.pipeline import scoring

    scoring._dnn_face_net.cache_clear()
    monkeypatch.setattr(scoring, "_DNN_WEIGHTS", tmp_path / "missing.caffemodel")
    try:
        with pytest.raises(ScoringError, match="weights missing"):
            dnn_face_detector(confidence_threshold=0.5)
    finally:
        scoring._dnn_face_net.cache_clear()
