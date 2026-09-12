"""Backfilling posters for recordings scored before thumbnail_path existed."""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

from apps.library.models import Video
from apps.library.storage_paths import build_originals_relative_path, to_absolute_storage_path

User = get_user_model()


@pytest.fixture
def storage_root(tmp_path, settings):
    settings.NAKAVID_STORAGE_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def user(db):
    return User.objects.create_user(username="backfill", password="secret123!")


def _scored_video(*, storage_root: Path, user, title: str, thumbnail_path: str = "") -> Video:
    relative_path = build_originals_relative_path(
        recorded_at=timezone.now().date(), filename=f"{title}.mp4"
    )
    (storage_root / relative_path).parent.mkdir(parents=True, exist_ok=True)
    (storage_root / relative_path).write_bytes(b"fake-video")

    return Video.objects.create(
        title=title,
        source_path=to_absolute_storage_path(storage_root, relative_path),
        video_type=Video.VideoType.TYPE_A,
        orientation=Video.Orientation.LANDSCAPE,
        class_name="A",
        theme="B",
        recorded_at=timezone.now(),
        duration_seconds=120,
        highlight_score=70,
        thumbnail_path=thumbnail_path,
        is_private=True,
        created_by=user,
    )


@pytest.mark.django_db
def test_thumbnails_a_video_scored_before_the_field_existed(storage_root, user):
    video = _scored_video(storage_root=storage_root, user=user, title="lesson")

    with patch("apps.pipeline.handlers.run_ffmpeg_thumbnail") as run_thumbnail:
        call_command("backfill_video_thumbnails", stdout=StringIO())

    run_thumbnail.assert_called_once()
    video.refresh_from_db()
    assert video.thumbnail_path.endswith("lesson__thumb.jpg")


@pytest.mark.django_db
def test_skips_a_video_that_already_has_one(storage_root, user):
    _scored_video(
        storage_root=storage_root,
        user=user,
        title="lesson",
        thumbnail_path="/nakavid/originals/2026/07/07/lesson__thumb.jpg",
    )

    with patch("apps.pipeline.handlers.run_ffmpeg_thumbnail") as run_thumbnail:
        call_command("backfill_video_thumbnails", stdout=StringIO())

    run_thumbnail.assert_not_called()


@pytest.mark.django_db
def test_skips_short_recordings(storage_root, user):
    """A short recording gets its poster on its own clip, at score time."""
    video = _scored_video(storage_root=storage_root, user=user, title="clip")
    video.video_type = Video.VideoType.TYPE_B
    video.save(update_fields=["video_type"])

    with patch("apps.pipeline.handlers.run_ffmpeg_thumbnail") as run_thumbnail:
        call_command("backfill_video_thumbnails", stdout=StringIO())

    run_thumbnail.assert_not_called()


@pytest.mark.django_db
def test_dry_run_touches_nothing(storage_root, user):
    video = _scored_video(storage_root=storage_root, user=user, title="lesson")
    out = StringIO()

    with patch("apps.pipeline.handlers.run_ffmpeg_thumbnail") as run_thumbnail:
        call_command("backfill_video_thumbnails", "--dry-run", stdout=out)

    run_thumbnail.assert_not_called()
    video.refresh_from_db()
    assert video.thumbnail_path == ""
    assert "would thumbnail 1 video(s)" in out.getvalue()


@pytest.mark.django_db
def test_a_failed_thumbnail_does_not_stop_the_rest(storage_root, user):
    first = _scored_video(storage_root=storage_root, user=user, title="broken")
    second = _scored_video(storage_root=storage_root, user=user, title="fine")

    def fake_thumbnail(*, source_path, target_path, at_seconds):
        if "broken" in str(source_path):
            raise RuntimeError("ffmpeg failed")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(b"jpeg")

    err = StringIO()
    with patch("apps.pipeline.handlers.run_ffmpeg_thumbnail", side_effect=fake_thumbnail):
        call_command("backfill_video_thumbnails", stdout=StringIO(), stderr=err)

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.thumbnail_path == ""
    assert second.thumbnail_path != ""
    assert "failed" in err.getvalue()
