from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from apps.pipeline.models import ScoringParams
from apps.pipeline.scoring import (
    DEFAULT_DETECTION,
    DetectionSettings,
    SequentialFrameSampler,
    count_smiles_in_faces,
    downscale_to_width,
    haar_cascade,
)

# Starting weights for combining signals into one 0-100 quality score. Not
# yet exposed as ScoringParams fields — promote to tunables if real footage
# shows they need per-run adjustment, the way plateau_score_ratio and
# min_peak_score did for clip extraction.
SHARPNESS_WEIGHT = 0.35
FACE_WEIGHT = 0.15
SMILE_WEIGHT = 0.35
COMPOSITION_WEIGHT = 0.15

# A sharp 1080p face crop typically lands well north of this; a blurry one
# well under it — unvalidated against real footage. still_min_quality_score
# is where real tuning should happen once there's output to look at.
SHARPNESS_SCALE = 150.0
# A face filling at least this fraction of the frame reads as intentionally
# composed rather than a distant figure in the background.
TARGET_FACE_AREA_RATIO = 0.04


@dataclass(frozen=True)
class StillCandidate:
    at_seconds: float
    quality_score: float
    face_count: int
    has_smile: bool
    sharpness: float


def _laplacian_sharpness(frame: np.ndarray) -> float:
    return float(cv2.Laplacian(frame, cv2.CV_64F).var())


def _largest_face_area_ratio(faces, frame_shape: tuple[int, ...]) -> float:
    if not len(faces):
        return 0.0
    frame_area = frame_shape[0] * frame_shape[1]
    if frame_area <= 0:
        return 0.0
    largest = max(int(width) * int(height) for (_x, _y, width, height) in faces)
    return largest / frame_area


def score_still_frame(
    *,
    at_seconds: float,
    frame: np.ndarray,
    settings: DetectionSettings,
    face_cascade,
    smile_cascade,
) -> StillCandidate:
    """Score one already-decoded, detection-scaled frame as a still candidate."""
    faces = face_cascade.detectMultiScale(
        frame,
        scaleFactor=settings.face_scale_factor,
        minNeighbors=settings.face_min_neighbors,
    )
    smile_count = count_smiles_in_faces(
        frame=frame, faces=faces, smile_cascade=smile_cascade, settings=settings
    )
    sharpness = _laplacian_sharpness(frame)
    face_area_ratio = _largest_face_area_ratio(faces, frame.shape)

    sharpness_component = min(sharpness / SHARPNESS_SCALE, 1.0)
    face_component = 1.0 if len(faces) else 0.0
    smile_component = 1.0 if smile_count else 0.0
    composition_component = min(face_area_ratio / TARGET_FACE_AREA_RATIO, 1.0)

    quality_score = (
        sharpness_component * SHARPNESS_WEIGHT
        + face_component * FACE_WEIGHT
        + smile_component * SMILE_WEIGHT
        + composition_component * COMPOSITION_WEIGHT
    ) * 100.0

    return StillCandidate(
        at_seconds=at_seconds,
        quality_score=round(quality_score, 2),
        face_count=len(faces),
        has_smile=smile_count > 0,
        sharpness=round(sharpness, 2),
    )


def gather_still_candidates(
    *,
    video_path: Path,
    duration_seconds: float,
    params: ScoringParams,
    detection: DetectionSettings = DEFAULT_DETECTION,
) -> list[StillCandidate]:
    """Walk the whole recording once, scoring one frame per sample step.

    A separate, coarser pass than clip scoring's overlapping windows — a
    still needs a single representative frame per candidate instant, not a
    12-frame average — but reuses the same cascades and sequential decode.
    """
    step_seconds = max(float(params.still_sample_step_seconds), 0.001)
    face_cascade = haar_cascade("haarcascade_frontalface_default.xml")
    smile_cascade = haar_cascade("haarcascade_smile.xml")

    candidates: list[StillCandidate] = []
    with SequentialFrameSampler(video_path) as sampler:
        at_seconds = 0.0
        while at_seconds < duration_seconds:
            frames = sampler.frames_for(
                start_seconds=at_seconds, end_seconds=at_seconds + step_seconds, max_frames=1
            )
            if frames:
                frame = frames[0]
                if detection.max_width:
                    frame = downscale_to_width(frame, detection.max_width)
                candidates.append(
                    score_still_frame(
                        at_seconds=at_seconds,
                        frame=frame,
                        settings=detection,
                        face_cascade=face_cascade,
                        smile_cascade=smile_cascade,
                    )
                )
            at_seconds += step_seconds
    return candidates


def select_stills(
    *, candidates: list[StillCandidate], params: ScoringParams
) -> list[StillCandidate]:
    """Top still_count candidates, spaced by still_min_gap_seconds.

    Candidates are ranked highest-quality-first, so once one misses
    still_min_quality_score every remaining one does too — still_count is a
    ceiling, not a target to fill with weak frames.
    """
    if not candidates:
        return []

    min_gap_seconds = float(params.still_min_gap_seconds)
    min_quality_score = float(params.still_min_quality_score)
    ranked = sorted(candidates, key=lambda c: (c.quality_score, -c.at_seconds), reverse=True)

    selected: list[StillCandidate] = []
    for candidate in ranked:
        if len(selected) >= int(params.still_count):
            break
        if candidate.quality_score < min_quality_score:
            break
        if any(
            abs(candidate.at_seconds - other.at_seconds) < min_gap_seconds for other in selected
        ):
            continue
        selected.append(candidate)

    return sorted(selected, key=lambda c: c.at_seconds)
