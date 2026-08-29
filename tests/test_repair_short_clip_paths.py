"""The repair for short recordings transcoded before the pointer fix."""

from __future__ import annotations

from decimal import Decimal
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

from apps.library.models import Clip, Video

User = get_user_model()


def _short_recording(*, user, playback: str) -> Video:
    video = Video.objects.create(
        title="IMG_2856",
        source_path="/nakavid/originals/2026/07/20260701_a_b/IMG_2856.MOV",
        playback_path=playback,
        video_type=Video.VideoType.TYPE_B,
        orientation=Video.Orientation.PORTRAIT,
        class_name="A",
        theme="B",
        recorded_at=timezone.now(),
        duration_seconds=12,
        is_private=True,
        created_by=user,
    )
    Clip.objects.create(
        video=video,
        storage_path=video.source_path,
        start_seconds=Decimal("0.000"),
        end_seconds=Decimal("12.000"),
        created_by=user,
    )
    return video


@pytest.fixture
def user(db):
    return User.objects.create_user(username="repair", password="secret123!")


@pytest.mark.django_db
def test_repoints_a_stale_clip(user):
    video = _short_recording(
        user=user, playback="/nakavid/originals/2026/07/20260701_a_b/IMG_2856__web.mp4"
    )

    call_command("repair_short_clip_paths", stdout=StringIO())

    clip = video.clips.get()
    assert clip.storage_path == video.playback_path


@pytest.mark.django_db
def test_is_idempotent(user):
    video = _short_recording(
        user=user, playback="/nakavid/originals/2026/07/20260701_a_b/IMG_2856__web.mp4"
    )

    call_command("repair_short_clip_paths", stdout=StringIO())
    second = StringIO()
    call_command("repair_short_clip_paths", stdout=second)

    assert "repointed 0 clip(s)" in second.getvalue()
    assert video.clips.get().storage_path == video.playback_path


@pytest.mark.django_db
def test_dry_run_changes_nothing(user):
    video = _short_recording(
        user=user, playback="/nakavid/originals/2026/07/20260701_a_b/IMG_2856__web.mp4"
    )
    out = StringIO()

    call_command("repair_short_clip_paths", "--dry-run", stdout=out)

    assert "would repoint 1 clip(s)" in out.getvalue()
    assert video.clips.get().storage_path == video.source_path


@pytest.mark.django_db
def test_leaves_untranscoded_recordings_alone(user):
    """No rendition means the source is the only playable file there is."""
    video = _short_recording(user=user, playback="")

    call_command("repair_short_clip_paths", stdout=StringIO())

    assert video.clips.get().storage_path == video.source_path
