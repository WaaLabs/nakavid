"""Re-queuing already-processed HDR videos to fix washed-out colors."""

from __future__ import annotations

from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

from apps.library.models import Clip, Video
from apps.library.storage_paths import build_originals_relative_path, to_absolute_storage_path
from apps.pipeline.models import Job

User = get_user_model()


@pytest.fixture
def storage_root(tmp_path, settings):
    settings.NAKAVID_STORAGE_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def user(db):
    return User.objects.create_user(username="hdr-backfill", password="secret123!")


def _video(
    *,
    storage_root: Path,
    user,
    title: str,
    video_type: str = Video.VideoType.TYPE_A,
    playback_path: str = "/nakavid/originals/2026/07/07/x__web.mp4",
    thumbnail_path: str = "/nakavid/originals/2026/07/07/x__thumb.jpg",
    write_source: bool = True,
) -> Video:
    relative_path = build_originals_relative_path(
        recorded_at=timezone.now().date(), filename=f"{title}.mov"
    )
    if write_source:
        (storage_root / relative_path).parent.mkdir(parents=True, exist_ok=True)
        (storage_root / relative_path).write_bytes(b"fake-video")

    return Video.objects.create(
        title=title,
        source_path=to_absolute_storage_path(storage_root, relative_path),
        video_type=video_type,
        orientation=Video.Orientation.LANDSCAPE,
        class_name="A",
        theme="B",
        recorded_at=timezone.now(),
        duration_seconds=120,
        playback_path=playback_path,
        thumbnail_path=thumbnail_path,
        is_private=True,
        created_by=user,
    )


def _patch_transfer(transfer: str):
    return patch(
        "apps.library.management.commands.backfill_hdr_color.probe_color_transfer",
        return_value=transfer,
    )


@pytest.mark.django_db
def test_requeues_an_hdr_video_and_clears_its_thumbnail(storage_root, user):
    video = _video(storage_root=storage_root, user=user, title="hdr")

    with _patch_transfer("smpte2084"):
        call_command("backfill_hdr_color", stdout=StringIO())

    video.refresh_from_db()
    assert video.thumbnail_path == ""
    assert video.jobs.filter(job_type=Job.JobType.TRANSCODE).exists()


@pytest.mark.django_db
def test_skips_an_sdr_video(storage_root, user):
    video = _video(storage_root=storage_root, user=user, title="sdr")

    with _patch_transfer("bt709"):
        call_command("backfill_hdr_color", stdout=StringIO())

    video.refresh_from_db()
    assert video.thumbnail_path != ""
    assert not video.jobs.exists()


@pytest.mark.django_db
def test_skips_hdr_video_with_no_playback_rendition(storage_root, user):
    video = _video(storage_root=storage_root, user=user, title="hdr-nowebm", playback_path="")

    err = StringIO()
    with _patch_transfer("arib-std-b67"):
        call_command("backfill_hdr_color", stdout=StringIO(), stderr=err)

    video.refresh_from_db()
    assert video.thumbnail_path != ""
    assert not video.jobs.exists()
    assert "no playback rendition" in err.getvalue()


@pytest.mark.django_db
def test_clears_a_short_recordings_clip_thumbnail_too(storage_root, user):
    video = _video(
        storage_root=storage_root, user=user, title="short", video_type=Video.VideoType.TYPE_B
    )
    clip = Clip.objects.create(
        video=video,
        storage_path=video.playback_path,
        thumbnail_path="/nakavid/originals/2026/07/07/short__thumb.jpg",
        start_seconds=Decimal("0.000"),
        end_seconds=Decimal("10.000"),
        created_by=user,
    )

    with _patch_transfer("smpte2084"):
        call_command("backfill_hdr_color", stdout=StringIO())

    clip.refresh_from_db()
    assert clip.thumbnail_path == ""


@pytest.mark.django_db
def test_dry_run_touches_nothing(storage_root, user):
    video = _video(storage_root=storage_root, user=user, title="hdr")
    out = StringIO()

    with _patch_transfer("smpte2084"):
        call_command("backfill_hdr_color", "--dry-run", stdout=out)

    video.refresh_from_db()
    assert video.thumbnail_path != ""
    assert not video.jobs.exists()
    assert "would re-queue 1 video(s)" in out.getvalue()


@pytest.mark.django_db
def test_skips_a_video_whose_source_file_is_missing(storage_root, user):
    video = _video(storage_root=storage_root, user=user, title="gone", write_source=False)

    with _patch_transfer("smpte2084") as probe:
        call_command("backfill_hdr_color", stdout=StringIO())

    probe.assert_not_called()
    video.refresh_from_db()
    assert video.thumbnail_path != ""
    assert not video.jobs.exists()
