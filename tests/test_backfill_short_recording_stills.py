"""Backfilling stills for short recordings imported before STILL_EXTRACTION

was wired up for them."""

from __future__ import annotations

from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

from apps.library.models import Clip, Still, Video
from apps.library.storage_paths import build_originals_relative_path, to_absolute_storage_path
from apps.pipeline.models import Job, ScoringParams

User = get_user_model()


@pytest.fixture
def storage_root(tmp_path, settings):
    settings.NAKAVID_STORAGE_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def user(db):
    return User.objects.create_user(username="backfill", password="secret123!")


def _short_recording(*, storage_root: Path, user, title: str, probed: bool = True) -> Video:
    relative_path = build_originals_relative_path(
        recorded_at=timezone.now().date(), filename=f"{title}.MOV"
    )
    (storage_root / relative_path).parent.mkdir(parents=True, exist_ok=True)
    (storage_root / relative_path).write_bytes(b"fake-video")

    video = Video.objects.create(
        title=title,
        source_path=to_absolute_storage_path(storage_root, relative_path),
        video_type=Video.VideoType.TYPE_B,
        orientation=Video.Orientation.MIXED,
        recorded_at=timezone.now(),
        duration_seconds=20,
        width=1080 if probed else None,
        height=1920 if probed else None,
        is_private=True,
        created_by=user,
    )
    Clip.objects.create(
        video=video,
        storage_path=video.source_path,
        start_seconds=Decimal("0.000"),
        end_seconds=Decimal("20.000"),
        created_by=user,
    )
    return video


@pytest.mark.django_db
def test_queues_stills_for_an_already_imported_short_recording(storage_root, user):
    video = _short_recording(storage_root=storage_root, user=user, title="IMG_0001")

    call_command("backfill_short_recording_stills", stdout=StringIO())

    params = ScoringParams.objects.get()
    assert Job.objects.filter(
        video=video, job_type=Job.JobType.STILL_EXTRACTION, scoring_params_id=params.pk
    ).exists()


@pytest.mark.django_db
def test_skips_a_short_recording_not_yet_probed(storage_root, user):
    video = _short_recording(storage_root=storage_root, user=user, title="IMG_0002", probed=False)

    call_command("backfill_short_recording_stills", stdout=StringIO())

    assert not Job.objects.filter(video=video, job_type=Job.JobType.STILL_EXTRACTION).exists()


@pytest.mark.django_db
def test_skips_a_short_recording_that_already_has_a_still(storage_root, user):
    video = _short_recording(storage_root=storage_root, user=user, title="IMG_0003")
    params = ScoringParams.objects.get()
    Still.objects.create(
        video=video,
        storage_path="/nakavid/highlights/2026/07/20260701_a_b/IMG_0003__still_001__p1.jpg",
        capture_seconds=Decimal("5.000"),
        scoring_params=params,
        created_by=user,
    )

    call_command("backfill_short_recording_stills", stdout=StringIO())

    assert not Job.objects.filter(video=video, job_type=Job.JobType.STILL_EXTRACTION).exists()


@pytest.mark.django_db
def test_skips_a_long_recording(storage_root, user):
    video = _short_recording(storage_root=storage_root, user=user, title="IMG_0004")
    video.video_type = Video.VideoType.TYPE_A
    video.save(update_fields=["video_type"])

    call_command("backfill_short_recording_stills", stdout=StringIO())

    assert not Job.objects.filter(video=video, job_type=Job.JobType.STILL_EXTRACTION).exists()


@pytest.mark.django_db
def test_dry_run_touches_nothing(storage_root, user):
    video = _short_recording(storage_root=storage_root, user=user, title="IMG_0005")
    out = StringIO()

    call_command("backfill_short_recording_stills", "--dry-run", stdout=out)

    assert not Job.objects.filter(video=video, job_type=Job.JobType.STILL_EXTRACTION).exists()
    assert "would queue stills for 1 short recording(s)" in out.getvalue()


@pytest.mark.django_db
def test_limit_stops_early(storage_root, user):
    _short_recording(storage_root=storage_root, user=user, title="IMG_0006")
    _short_recording(storage_root=storage_root, user=user, title="IMG_0007")

    call_command("backfill_short_recording_stills", "--limit", "1", stdout=StringIO())

    assert Job.objects.filter(job_type=Job.JobType.STILL_EXTRACTION).count() == 1


@pytest.mark.django_db
def test_rerun_is_idempotent(storage_root, user):
    """A second run doesn't re-queue a video that already got one job."""
    video = _short_recording(storage_root=storage_root, user=user, title="IMG_0008")
    params = ScoringParams.objects.get()

    # Simulate the worker having already produced a still from the first run.
    call_command("backfill_short_recording_stills", stdout=StringIO())
    Still.objects.create(
        video=video,
        storage_path="/nakavid/highlights/2026/07/20260701_a_b/IMG_0008__still_001__p1.jpg",
        capture_seconds=Decimal("5.000"),
        scoring_params=params,
        created_by=user,
    )

    call_command("backfill_short_recording_stills", stdout=StringIO())

    assert Job.objects.filter(video=video, job_type=Job.JobType.STILL_EXTRACTION).count() == 1
