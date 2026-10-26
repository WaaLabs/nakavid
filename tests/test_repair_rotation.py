"""Re-reading stored rotation and re-transcoding upside-down videos."""

from __future__ import annotations

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
from apps.pipeline.probe import ProbeError, ProbeResult

User = get_user_model()


@pytest.fixture
def storage_root(tmp_path, settings):
    settings.NAKAVID_STORAGE_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def user(db):
    return User.objects.create_user(username="rotation-repair", password="secret123!")


def _video(
    *,
    storage_root: Path,
    user,
    title: str,
    rotation_degrees: int = 270,
    rotation_override_degrees: int | None = None,
    video_type: str = Video.VideoType.TYPE_A,
    playback_path: str = "/nakavid/originals/2026/07/07/x__web.mp4",
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
        orientation=Video.Orientation.PORTRAIT,
        recorded_at=timezone.now(),
        duration_seconds=12,
        rotation_degrees=rotation_degrees,
        rotation_override_degrees=rotation_override_degrees,
        playback_path=playback_path,
        thumbnail_path="/nakavid/originals/2026/07/07/x__thumb.jpg",
        is_private=True,
        created_by=user,
    )


def _probe(rotation: int):
    return patch(
        "apps.library.management.commands.repair_rotation.run_ffprobe",
        return_value=ProbeResult(
            duration_seconds=12,
            orientation=Video.Orientation.PORTRAIT,
            video_codec="hevc",
            width=1080,
            height=1920,
            rotation=rotation,
        ),
    )


def _transcodes(video: Video) -> int:
    return video.jobs.filter(job_type=Job.JobType.TRANSCODE).count()


@pytest.mark.django_db
def test_corrects_rotation_clears_thumbnails_and_requeues(storage_root, user):
    video = _video(storage_root=storage_root, user=user, title="upside", video_type="type_b")
    clip = Clip.objects.create(
        video=video,
        storage_path=video.playback_path,
        start_seconds=0,
        end_seconds=12,
        thumbnail_path="/nakavid/originals/2026/07/07/x__clipthumb.jpg",
        created_by=user,
    )

    with _probe(90):
        call_command("repair_rotation", stdout=StringIO())

    video.refresh_from_db()
    clip.refresh_from_db()
    assert video.rotation_degrees == 90
    assert video.thumbnail_path == ""
    assert clip.thumbnail_path == ""
    assert _transcodes(video) == 1


@pytest.mark.django_db
def test_is_idempotent(storage_root, user):
    video = _video(storage_root=storage_root, user=user, title="upside")

    with _probe(90):
        call_command("repair_rotation", stdout=StringIO())
        call_command("repair_rotation", stdout=StringIO())

    assert _transcodes(video) == 1


@pytest.mark.django_db
def test_leaves_a_correct_video_alone(storage_root, user):
    video = _video(storage_root=storage_root, user=user, title="fine", rotation_degrees=180)

    with _probe(180):
        call_command("repair_rotation", stdout=StringIO())

    video.refresh_from_db()
    assert video.thumbnail_path != ""
    assert not video.jobs.exists()


@pytest.mark.django_db
def test_dry_run_changes_nothing(storage_root, user):
    video = _video(storage_root=storage_root, user=user, title="upside")
    out = StringIO()

    with _probe(90):
        call_command("repair_rotation", dry_run=True, stdout=out)

    video.refresh_from_db()
    assert video.rotation_degrees == 270
    assert not video.jobs.exists()
    assert "would re-transcode: video" in out.getvalue()
    assert "would correct 1 video(s), 1 re-transcode(s) queued" in out.getvalue()


@pytest.mark.django_db
def test_a_stale_stored_value_that_isnt_the_bugs_mirror_is_only_corrected(storage_root, user):
    """A 0 left by a row predating persisted rotation rendered fine via autorotate."""
    video = _video(storage_root=storage_root, user=user, title="old", rotation_degrees=0)

    with _probe(180):
        call_command("repair_rotation", stdout=StringIO())

    video.refresh_from_db()
    assert video.rotation_degrees == 180
    assert video.thumbnail_path != ""
    assert not video.jobs.exists()


@pytest.mark.django_db
def test_an_override_only_gets_its_stored_value_fixed(storage_root, user):
    """The override already decides the render; re-transcoding would change nothing."""
    video = _video(
        storage_root=storage_root, user=user, title="overridden", rotation_override_degrees=180
    )

    with _probe(90):
        call_command("repair_rotation", stdout=StringIO())

    video.refresh_from_db()
    assert video.rotation_degrees == 90
    assert video.thumbnail_path != ""
    assert not video.jobs.exists()


@pytest.mark.django_db
def test_a_video_awaiting_its_first_transcode_is_not_queued_twice(storage_root, user):
    video = _video(storage_root=storage_root, user=user, title="pending")
    Job.objects.create(video=video, job_type=Job.JobType.TRANSCODE)

    with _probe(90):
        call_command("repair_rotation", stdout=StringIO())

    video.refresh_from_db()
    assert video.rotation_degrees == 90
    assert _transcodes(video) == 1


@pytest.mark.django_db
def test_a_video_with_no_playback_rendition_is_not_queued(storage_root, user):
    video = _video(storage_root=storage_root, user=user, title="browser-safe", playback_path="")

    with _probe(90):
        call_command("repair_rotation", stdout=StringIO())

    video.refresh_from_db()
    assert video.rotation_degrees == 90
    assert not video.jobs.exists()


@pytest.mark.django_db
def test_missing_and_unreadable_sources_are_skipped(storage_root, user):
    _video(storage_root=storage_root, user=user, title="gone", write_source=False)
    unreadable = _video(storage_root=storage_root, user=user, title="corrupt")
    out, err = StringIO(), StringIO()

    with patch(
        "apps.library.management.commands.repair_rotation.run_ffprobe",
        side_effect=ProbeError("bad file"),
    ):
        call_command("repair_rotation", stdout=out, stderr=err)

    assert not unreadable.jobs.exists()
    assert "1 skipped (source file missing), 1 unreadable" in out.getvalue()
    assert "bad file" in err.getvalue()
