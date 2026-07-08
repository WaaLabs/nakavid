from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import librosa
import numpy as np

from apps.pipeline.models import ScoringParams


class ScoringError(Exception):
    """Raised when segment scoring cannot complete."""


@dataclass(frozen=True)
class WindowSignals:
    face_count: float
    smile_ratio: float
    motion_energy: float
    audio_rms: float


@dataclass(frozen=True)
class WindowScore:
    start_seconds: float
    end_seconds: float
    score: float
    signals: WindowSignals


@dataclass(frozen=True)
class SegmentScoringResult:
    energy_curve: list[dict]
    highlight_score: int


def build_windows(
    *,
    duration_seconds: float,
    window_size_seconds: float,
    step_seconds: float,
) -> list[tuple[float, float]]:
    if duration_seconds <= 0:
        return []

    windows: list[tuple[float, float]] = []
    start = 0.0
    while start < duration_seconds:
        end = min(start + window_size_seconds, duration_seconds)
        if end > start:
            windows.append((start, end))
        if end >= duration_seconds:
            break
        start += step_seconds

    return windows


def aggregate_window_score(*, signals: WindowSignals, params: ScoringParams) -> float:
    face_component = min(signals.face_count / 3.0, 1.0)
    smile_component = min(max(signals.smile_ratio, 0.0), 1.0)
    motion_component = min(max(signals.motion_energy, 0.0), 1.0)
    audio_component = min(max(signals.audio_rms * 10.0, 0.0), 1.0)

    weighted = (
        face_component * float(params.face_weight)
        + smile_component * float(params.smile_weight)
        + motion_component * float(params.motion_weight)
        + audio_component * float(params.audio_weight)
    )

    score = weighted * 100.0
    if signals.audio_rms < float(params.silence_rms_threshold):
        score -= float(params.silence_penalty_weight) * 100.0

    return max(0.0, min(score, 100.0))


def smooth_scores(scores: list[float], *, window_size: int = 3) -> list[float]:
    if not scores:
        return []

    if window_size <= 1:
        return list(scores)

    smoothed: list[float] = []
    half = window_size // 2
    for index in range(len(scores)):
        start = max(0, index - half)
        end = min(len(scores), index + half + 1)
        smoothed.append(sum(scores[start:end]) / (end - start))

    return smoothed


def _haar_cascade(name: str) -> cv2.CascadeClassifier:
    cascade_path = Path(cv2.data.haarcascades) / name
    classifier = cv2.CascadeClassifier(str(cascade_path))
    if classifier.empty():
        raise ScoringError(f"OpenCV Haar cascade unavailable: {name}")
    return classifier


def _sample_frames(
    *,
    video_path: Path,
    start_seconds: float,
    end_seconds: float,
    max_frames: int = 12,
) -> list[np.ndarray]:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise ScoringError(f"Unable to open video for scoring: {video_path}")

    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        if not fps or fps <= 0:
            fps = 25.0

        start_frame = int(start_seconds * fps)
        end_frame = max(start_frame + 1, int(end_seconds * fps))
        frame_count = max(end_frame - start_frame, 1)
        step = max(frame_count // max_frames, 1)

        frames: list[np.ndarray] = []
        for frame_index in range(start_frame, end_frame, step):
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = capture.read()
            if not ok or frame is None:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            frames.append(gray)
            if len(frames) >= max_frames:
                break

        return frames
    finally:
        capture.release()


def extract_window_signals(
    *,
    video_path: Path,
    start_seconds: float,
    end_seconds: float,
) -> WindowSignals:
    frames = _sample_frames(
        video_path=video_path,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
    )
    face_cascade = _haar_cascade("haarcascade_frontalface_default.xml")
    smile_cascade = _haar_cascade("haarcascade_smile.xml")

    face_total = 0
    smile_total = 0
    motion_total = 0.0
    previous_frame: np.ndarray | None = None

    for frame in frames:
        faces = face_cascade.detectMultiScale(frame, scaleFactor=1.1, minNeighbors=4)
        face_total += len(faces)

        smiles = smile_cascade.detectMultiScale(frame, scaleFactor=1.7, minNeighbors=20)
        smile_total += len(smiles)

        if previous_frame is not None:
            diff = cv2.absdiff(frame, previous_frame)
            motion_total += float(np.mean(diff)) / 255.0
        previous_frame = frame

    frame_count = max(len(frames), 1)
    motion_pairs = max(len(frames) - 1, 1)

    try:
        audio, _sample_rate = librosa.load(
            str(video_path),
            sr=None,
            mono=True,
            offset=start_seconds,
            duration=max(end_seconds - start_seconds, 0.001),
        )
        audio_rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
    except Exception as exc:
        raise ScoringError(f"Unable to load audio for scoring: {exc}") from exc

    return WindowSignals(
        face_count=face_total / frame_count,
        smile_ratio=smile_total / frame_count,
        motion_energy=motion_total / motion_pairs,
        audio_rms=audio_rms,
    )


def score_windows(
    *,
    duration_seconds: float,
    params: ScoringParams,
    signal_loader,
) -> SegmentScoringResult:
    windows = build_windows(
        duration_seconds=duration_seconds,
        window_size_seconds=float(params.window_size_seconds),
        step_seconds=float(params.step_seconds),
    )

    raw_scores: list[WindowScore] = []
    for start_seconds, end_seconds in windows:
        signals = signal_loader(start_seconds, end_seconds)
        score = aggregate_window_score(signals=signals, params=params)
        raw_scores.append(
            WindowScore(
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                score=score,
                signals=signals,
            )
        )

    smoothed = smooth_scores([window.score for window in raw_scores])
    energy_curve: list[dict] = []
    for window, score in zip(raw_scores, smoothed, strict=True):
        energy_curve.append(
            {
                "start": round(window.start_seconds, 3),
                "end": round(window.end_seconds, 3),
                "score": round(score, 2),
                "signals": {
                    "face_count": round(window.signals.face_count, 4),
                    "smile_ratio": round(window.signals.smile_ratio, 4),
                    "motion_energy": round(window.signals.motion_energy, 4),
                    "audio_rms": round(window.signals.audio_rms, 6),
                },
            }
        )

    highlight_score = int(round(max(smoothed))) if smoothed else 0
    highlight_score = max(0, min(highlight_score, 100))

    return SegmentScoringResult(
        energy_curve=energy_curve,
        highlight_score=highlight_score,
    )


def run_segment_scoring(
    *,
    video_path: Path,
    params: ScoringParams,
    duration_seconds: int,
) -> SegmentScoringResult:
    if duration_seconds <= 0:
        raise ScoringError("Video duration must be positive before scoring")

    def signal_loader(start_seconds: float, end_seconds: float) -> WindowSignals:
        return extract_window_signals(
            video_path=video_path,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
        )

    return score_windows(
        duration_seconds=float(duration_seconds),
        params=params,
        signal_loader=signal_loader,
    )


def get_active_scoring_params() -> ScoringParams:
    params = ScoringParams.objects.order_by("-pk").first()
    if params is None:
        raise ScoringError("No ScoringParams row configured")
    return params


def scoring_params_from_job(job) -> ScoringParams:
    if job.scoring_params_id is not None:
        return job.scoring_params
    return get_active_scoring_params()
