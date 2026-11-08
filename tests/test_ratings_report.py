"""ratings_report summarises signals on rated-up vs rated-down picks."""

from __future__ import annotations

from decimal import Decimal
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

from apps.library.management.commands.ratings_report import CLIP_METRICS, compare
from apps.library.models import Clip, Still, Video
from apps.pipeline.models import ScoringParams

User = get_user_model()


@pytest.fixture
def video(db):
    user = User.objects.create_user(username="rater", password="secret123!")
    return Video.objects.create(
        title="Rated Lesson",
        source_path="/nakavid/originals/2026/08/26/lesson.mp4",
        video_type=Video.VideoType.TYPE_A,
        orientation=Video.Orientation.LANDSCAPE,
        recorded_at=timezone.now(),
        duration_seconds=600,
        width=1920,
        height=1080,
        created_by=user,
    )


def _clip(video, *, rating, score, motion, params=None, start=0) -> Clip:
    return Clip.objects.create(
        video=video,
        storage_path=f"/nakavid/highlights/2026/08/26/lesson__clip_{Clip.objects.count():03d}.mp4",
        start_seconds=Decimal(start),
        end_seconds=Decimal(start + 20),
        highlight_score=score,
        energy_curve=[
            {"start": start, "end": start + 4, "score": 10.0, "signals": {"motion_energy": 0.0}},
            {
                "start": start + 2,
                "end": start + 6,
                "score": float(score),
                "signals": {
                    "face_count": 3.0,
                    "smile_ratio": 0.5,
                    "motion_energy": motion,
                    "audio_rms": 0.05,
                },
            },
        ],
        scoring_params=params,
        created_by=video.created_by,
        rating=rating,
    )


def _still(video, *, rating, sharpness, obstructed=False) -> Still:
    return Still.objects.create(
        video=video,
        storage_path=f"/nakavid/highlights/2026/08/26/lesson__still_{Still.objects.count():03d}.jpg",
        capture_seconds=Decimal("10"),
        quality_score=50,
        face_count=1,
        smile_count=1,
        sharpness=sharpness,
        obstructed=obstructed,
        created_by=video.created_by,
        rating=rating,
    )


def _run() -> str:
    out = StringIO()
    call_command("ratings_report", stdout=out)
    return out.getvalue()


@pytest.mark.django_db
def test_compare_uses_the_peak_window_and_reports_the_gap(video):
    clips = [
        _clip(video, rating=True, score=80, motion=0.2, start=0),
        _clip(video, rating=True, score=70, motion=0.1, start=30),
        _clip(video, rating=False, score=75, motion=0.05, start=60),
        _clip(video, rating=None, score=60, motion=0.0, start=90),
    ]

    rows = {row[0]: row for row in compare(clips, CLIP_METRICS)}

    assert rows[""][1:4] == ["up (n=2)", "down (n=1)", "unrated (n=1)"]
    assert rows["score"][1:] == ["75.0", "75.0", "60.0", "+0.0"]
    # From the score-80/70/75 peak windows, not the low-score lead-in window.
    assert rows["peak motion_energy"][1:] == ["0.150", "0.050", "0.000", "+0.100"]
    assert rows["length s"][1] == "20.0"


@pytest.mark.django_db
def test_clips_are_split_by_scoring_params_run(video):
    fixed = ScoringParams.objects.get()
    variable = ScoringParams.objects.create(clip_length_mode="variable")
    _clip(video, rating=True, score=80, motion=0.2, params=fixed, start=0)
    _clip(video, rating=False, score=70, motion=0.1, params=variable, start=30)

    output = _run()

    assert f"ScoringParams #{fixed.pk} (Fixed length)" in output
    assert f"ScoringParams #{variable.pk} (Variable length)" in output


@pytest.mark.django_db
def test_stills_section_and_thin_data_warning(video):
    _still(video, rating=True, sharpness=150.0)
    _still(video, rating=False, sharpness=10.0, obstructed=True)
    _still(video, rating=None, sharpness=100.0)

    output = _run()

    assert "2 of 3 rated, 50% of rated are up" in output
    assert "Only 1 up / 1 down rated" in output
    sharpness = next(line for line in output.splitlines() if line.startswith("sharpness"))
    assert sharpness.split()[1:] == ["150.0", "10.0", "100.0", "+140.0"]
    obstructed = next(line for line in output.splitlines() if line.startswith("obstructed"))
    assert obstructed.split()[-1] == "-100"


@pytest.mark.django_db
def test_enough_ratings_drops_the_warning(video):
    for _ in range(10):
        _still(video, rating=True, sharpness=150.0)
        _still(video, rating=False, sharpness=10.0)

    assert "treat any gap as a hint" not in _run()


@pytest.mark.django_db
def test_empty_library_reports_cleanly(db):
    output = _run()

    assert "Stills" in output
    assert "None yet." in output
    assert "No clips yet." in output
