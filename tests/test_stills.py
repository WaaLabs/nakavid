from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from apps.library.storage_paths import build_still_relative_path
from apps.pipeline.models import ScoringParams
from apps.pipeline.scoring import DEFAULT_DETECTION
from apps.pipeline.stills import (
    StillCandidate,
    gather_still_candidates,
    score_still_frame,
    select_stills,
)


class _FakeCascade:
    def __init__(self, detections):
        self._detections = detections

    def detectMultiScale(self, *args, **kwargs):
        return self._detections


def _sharp_frame(size: int = 200) -> np.ndarray:
    rng = np.random.default_rng(0)
    return rng.integers(0, 255, size=(size, size), dtype=np.uint8)


def _blurry_frame(size: int = 200) -> np.ndarray:
    return np.full((size, size), 128, dtype=np.uint8)


def test_score_still_frame_rewards_sharp_smiling_well_composed():
    frame = _sharp_frame()
    # A face filling a large chunk of a 200x200 frame — well over the 4%
    # composition target.
    face_cascade = _FakeCascade([(20, 20, 100, 100)])
    smile_cascade = _FakeCascade([(0, 0, 10, 10)])

    candidate = score_still_frame(
        at_seconds=12.0,
        frame=frame,
        settings=DEFAULT_DETECTION,
        face_cascade=face_cascade,
        smile_cascade=smile_cascade,
    )

    assert candidate.at_seconds == 12.0
    assert candidate.face_count == 1
    assert candidate.has_smile is True
    assert candidate.quality_score > 70.0


def test_score_still_frame_penalizes_blurry_no_face_frame():
    frame = _blurry_frame()
    face_cascade = _FakeCascade([])
    smile_cascade = _FakeCascade([])

    candidate = score_still_frame(
        at_seconds=5.0,
        frame=frame,
        settings=DEFAULT_DETECTION,
        face_cascade=face_cascade,
        smile_cascade=smile_cascade,
    )

    assert candidate.face_count == 0
    assert candidate.has_smile is False
    assert candidate.quality_score < 10.0


class _FakeSampler:
    def __init__(self, frames_by_start: dict[float, list[np.ndarray]]):
        self._frames_by_start = frames_by_start

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def frames_for(self, *, start_seconds, end_seconds, max_frames):
        return self._frames_by_start.get(round(start_seconds, 3), [])


@pytest.mark.django_db
def test_gather_still_candidates_samples_once_per_step():
    params = ScoringParams.objects.get()
    params.still_sample_step_seconds = 2

    frames_by_start = {0.0: [_sharp_frame()], 2.0: [_sharp_frame()], 4.0: [_sharp_frame()]}
    fake_sampler = _FakeSampler(frames_by_start)

    with (
        patch("apps.pipeline.stills.SequentialFrameSampler", return_value=fake_sampler),
        patch("apps.pipeline.stills.haar_cascade", return_value=_FakeCascade([])),
    ):
        candidates = gather_still_candidates(
            video_path=Path("/nowhere.mp4"),
            duration_seconds=6.0,
            params=params,
        )

    assert [c.at_seconds for c in candidates] == [0.0, 2.0, 4.0]


@pytest.mark.django_db
def test_select_stills_respects_count_and_min_gap():
    params = ScoringParams.objects.get()
    params.still_count = 2
    params.still_min_gap_seconds = 10

    candidates = [
        StillCandidate(
            at_seconds=0.0, quality_score=90.0, face_count=1, has_smile=True, sharpness=200.0
        ),
        StillCandidate(
            at_seconds=2.0, quality_score=85.0, face_count=1, has_smile=True, sharpness=190.0
        ),
        StillCandidate(
            at_seconds=40.0, quality_score=70.0, face_count=1, has_smile=True, sharpness=180.0
        ),
    ]

    selected = select_stills(candidates=candidates, params=params)

    # The 2.0s candidate is within still_min_gap_seconds of the 0.0s winner,
    # so the next distinct pick is the 40.0s one.
    assert [c.at_seconds for c in selected] == [0.0, 40.0]


@pytest.mark.django_db
def test_select_stills_min_quality_score_stops_filling_still_count():
    params = ScoringParams.objects.get()
    params.still_count = 8
    params.still_min_gap_seconds = 1
    params.still_min_quality_score = 60

    candidates = [
        StillCandidate(
            at_seconds=0.0, quality_score=90.0, face_count=1, has_smile=True, sharpness=200.0
        ),
        StillCandidate(
            at_seconds=10.0, quality_score=40.0, face_count=0, has_smile=False, sharpness=20.0
        ),
    ]

    selected = select_stills(candidates=candidates, params=params)

    assert len(selected) == 1
    assert selected[0].at_seconds == 0.0


def test_build_still_relative_path():
    from datetime import date

    relative = build_still_relative_path(
        recorded_at=date(2026, 8, 26), source_stem="IMG_8443", still_index=3
    )

    assert relative == "highlights/2026/08/26/IMG_8443__still_003.jpg"


def test_build_still_relative_path_variant_avoids_collisions_across_params():
    from datetime import date

    a = build_still_relative_path(
        recorded_at=date(2026, 8, 26), source_stem="IMG_8443", still_index=1, variant="p3"
    )
    b = build_still_relative_path(
        recorded_at=date(2026, 8, 26), source_stem="IMG_8443", still_index=1, variant="p4"
    )

    assert a != b
    assert a == "highlights/2026/08/26/IMG_8443__still_001__p3.jpg"
