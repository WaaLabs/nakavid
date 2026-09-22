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
    assert candidate.smile_count == 1
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
    assert candidate.smile_count == 0
    assert candidate.quality_score < 10.0


class _SequencedSmileCascade:
    """Returns a smile hit (or not) per call, in order — count_smiles_in_faces
    calls detectMultiScale once per face's mouth ROI, so this simulates a
    specific number of faces actually smiling."""

    def __init__(self, per_face_has_smile: list[bool]):
        self._results = iter(per_face_has_smile)

    def detectMultiScale(self, *args, **kwargs):
        return [(0, 0, 10, 10)] if next(self._results, False) else []


def test_score_still_frame_smile_count_beats_one_false_positive():
    """One noisy smile hit among several faces should not score the same as
    several faces genuinely smiling — a boolean has_smile collapsed exactly
    this distinction (caught on real footage: a frame with 1 smile hit out
    of 5 faces, none of them actually smiling, scored almost as high as a
    frame with 4 smiles out of 9)."""
    frame = _sharp_frame()
    five_faces = _FakeCascade([(i * 20, 0, 30, 30) for i in range(5)])

    several_smiling = score_still_frame(
        at_seconds=1.0,
        frame=frame,
        settings=DEFAULT_DETECTION,
        face_cascade=five_faces,
        smile_cascade=_SequencedSmileCascade([True, True, True, True, False]),
    )
    one_false_positive = score_still_frame(
        at_seconds=2.0,
        frame=frame,
        settings=DEFAULT_DETECTION,
        face_cascade=five_faces,
        smile_cascade=_SequencedSmileCascade([True, False, False, False, False]),
    )

    assert several_smiling.smile_count == 4
    assert one_false_positive.smile_count == 1
    assert several_smiling.quality_score > one_false_positive.quality_score


def test_score_still_frame_smile_count_does_not_punish_group_size():
    """A ratio would score a 5-face frame with 3 genuine smiles below a
    1-face frame with a single lucky hit (3/5 < 1/1) — a rich group shot
    with several real smiles should not lose to a single-face frame just
    for having more people in it."""
    frame = _sharp_frame()

    busy_group = score_still_frame(
        at_seconds=1.0,
        frame=frame,
        settings=DEFAULT_DETECTION,
        face_cascade=_FakeCascade([(i * 20, 0, 30, 30) for i in range(5)]),
        smile_cascade=_SequencedSmileCascade([True, True, True, False, False]),
    )
    single_face = score_still_frame(
        at_seconds=2.0,
        frame=frame,
        settings=DEFAULT_DETECTION,
        face_cascade=_FakeCascade([(20, 20, 30, 30)]),
        smile_cascade=_SequencedSmileCascade([True]),
    )

    assert busy_group.quality_score >= single_face.quality_score


def _frame_with_border_blob(size: int = 240) -> np.ndarray:
    """A sharp random-noise frame with a large flat (out-of-focus-looking)
    block covering the right third, touching every row — the shape of a
    phone-camera holder's own arm or torso blocking part of the shot."""
    rng = np.random.default_rng(1)
    frame = rng.integers(0, 255, size=(size, size), dtype=np.uint8)
    frame[:, (size * 2) // 3 :] = 130
    return frame


def test_obstructed_frame_is_penalized():
    frame = _frame_with_border_blob()
    face_cascade = _FakeCascade([(10, 10, 60, 60)])
    smile_cascade = _FakeCascade([(0, 0, 5, 5)])

    obstructed = score_still_frame(
        at_seconds=1.0,
        frame=frame,
        settings=DEFAULT_DETECTION,
        face_cascade=face_cascade,
        smile_cascade=smile_cascade,
    )
    clean = score_still_frame(
        at_seconds=2.0,
        frame=_sharp_frame(size=240),
        settings=DEFAULT_DETECTION,
        face_cascade=face_cascade,
        smile_cascade=smile_cascade,
    )

    assert obstructed.obstructed is True
    assert clean.obstructed is False
    assert obstructed.quality_score < clean.quality_score


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
            at_seconds=0.0,
            quality_score=90.0,
            face_count=1,
            smile_count=1,
            sharpness=200.0,
            obstructed=False,
        ),
        StillCandidate(
            at_seconds=2.0,
            quality_score=85.0,
            face_count=1,
            smile_count=1,
            sharpness=190.0,
            obstructed=False,
        ),
        StillCandidate(
            at_seconds=40.0,
            quality_score=70.0,
            face_count=1,
            smile_count=1,
            sharpness=180.0,
            obstructed=False,
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
            at_seconds=0.0,
            quality_score=90.0,
            face_count=1,
            smile_count=1,
            sharpness=200.0,
            obstructed=False,
        ),
        StillCandidate(
            at_seconds=10.0,
            quality_score=40.0,
            face_count=0,
            smile_count=0,
            sharpness=20.0,
            obstructed=False,
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
