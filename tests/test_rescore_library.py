"""rescore_library re-applies the active params to stored curves."""

from __future__ import annotations

from decimal import Decimal
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

from apps.library.models import Clip, Video
from apps.pipeline.models import Job, ScoringParams

User = get_user_model()


def _curve(face_count: float) -> list[dict]:
    signals = {
        "face_count": face_count,
        "smile_ratio": 0.0,
        "motion_energy": 0.1,
        "audio_rms": 0.05,
    }
    return [
        {"start": i * 2.0, "end": i * 2.0 + 4.0, "score": 99.0, "signals": signals}
        for i in range(10)
    ]


@pytest.fixture
def user(db):
    return User.objects.create_user(username="rescorer", password="secret123!")


def _video(user, *, name: str, video_type: str, face_count: float = 3.0) -> Video:
    return Video.objects.create(
        title=name,
        source_path=f"/nakavid/originals/2026/08/26/{name}.mp4",
        video_type=video_type,
        orientation=Video.Orientation.LANDSCAPE,
        recorded_at=timezone.now(),
        duration_seconds=24,
        width=1920,
        height=1080,
        created_by=user,
        energy_curve=_curve(face_count),
        highlight_score=99,
    )


def _clip(video: Video, *, params: ScoringParams | None, rating: bool | None = None) -> Clip:
    return Clip.objects.create(
        video=video,
        storage_path=f"/nakavid/highlights/{video.title}__clip_{Clip.objects.count():03d}.mp4",
        start_seconds=Decimal("0"),
        end_seconds=Decimal("4"),
        highlight_score=99,
        energy_curve=video.energy_curve,
        scoring_params=params,
        created_by=video.created_by,
        rating=rating,
    )


def _run(*args: str) -> str:
    out = StringIO()
    call_command("rescore_library", *args, stdout=out)
    return out.getvalue()


# Default saturation: face 3/6=0.5, smile 0, motion 0.1/0.2=0.5, audio 0.05/0.1=0.5.
EXPECTED_WINDOW_SCORE = (0.5 + 0.0 + 0.5 + 0.5) / 4 * 100


def _expected_score() -> int:
    return round(EXPECTED_WINDOW_SCORE)


@pytest.mark.django_db
def test_rescores_long_recording_and_queues_extraction_per_params_row(user):
    active = ScoringParams.objects.get()
    variable = ScoringParams.objects.create(clip_length_mode="variable")
    video = _video(user, name="long", video_type=Video.VideoType.TYPE_A)
    _clip(video, params=active)
    _clip(video, params=variable)

    output = _run()

    video.refresh_from_db()
    assert video.highlight_score == _expected_score()
    assert all(
        point["score"] == pytest.approx(EXPECTED_WINDOW_SCORE) for point in video.energy_curve
    )
    jobs = Job.objects.filter(video=video, job_type=Job.JobType.CLIP_EXTRACTION)
    assert sorted(jobs.values_list("scoring_params_id", flat=True)) == sorted(
        [active.pk, variable.pk]
    )
    assert "Re-scored 1 video(s)" in output


@pytest.mark.django_db
def test_long_recording_without_clips_queues_the_active_row(user):
    ScoringParams.objects.create()
    active = ScoringParams.objects.order_by("-pk").first()
    video = _video(user, name="unclipped", video_type=Video.VideoType.TYPE_A)

    _run()

    job = Job.objects.get(video=video)
    assert job.scoring_params_id == active.pk


@pytest.mark.django_db
def test_short_recording_updates_its_own_clip_and_queues_nothing(user):
    video = _video(user, name="short", video_type=Video.VideoType.TYPE_B)
    clip = _clip(video, params=None)

    _run()

    clip.refresh_from_db()
    assert clip.highlight_score == _expected_score()
    assert clip.energy_curve[0]["score"] == pytest.approx(EXPECTED_WINDOW_SCORE)
    assert not Job.objects.filter(video=video).exists()


@pytest.mark.django_db
def test_rated_long_recordings_are_skipped_unless_asked(user):
    params = ScoringParams.objects.get()
    video = _video(user, name="rated", video_type=Video.VideoType.TYPE_A)
    _clip(video, params=params, rating=True)

    output = _run()

    video.refresh_from_db()
    assert video.highlight_score == 99
    assert not Job.objects.exists()
    assert f"skipped (rated clips): {video.pk}" in output

    _run("--include-rated")

    video.refresh_from_db()
    assert video.highlight_score == _expected_score()
    assert Job.objects.filter(video=video).count() == 1


@pytest.mark.django_db
def test_dry_run_changes_nothing(user):
    video = _video(user, name="dry", video_type=Video.VideoType.TYPE_A)

    output = _run("--dry-run")

    video.refresh_from_db()
    assert video.highlight_score == 99
    assert video.energy_curve[0]["score"] == 99.0
    assert not Job.objects.exists()
    assert "Would re-score 1 video(s)" in output
    assert "1 clip-extraction job(s) would be queued" in output


@pytest.mark.django_db
def test_rerun_is_idempotent(user):
    video = _video(user, name="twice", video_type=Video.VideoType.TYPE_B)

    _run()
    video.refresh_from_db()
    first = (video.highlight_score, video.energy_curve)
    _run()
    video.refresh_from_db()

    assert (video.highlight_score, video.energy_curve) == first


@pytest.mark.django_db
def test_more_faces_now_outscore_fewer(user):
    """The point of the change: past three faces, more faces still counts."""
    crowded = _video(user, name="crowded", video_type=Video.VideoType.TYPE_B, face_count=6.0)
    sparse = _video(user, name="sparse", video_type=Video.VideoType.TYPE_B, face_count=3.0)

    _run()

    crowded.refresh_from_db()
    sparse.refresh_from_db()
    assert crowded.highlight_score > sparse.highlight_score
