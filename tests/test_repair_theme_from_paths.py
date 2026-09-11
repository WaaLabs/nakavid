"""The repair that drops class/theme from originals/highlights folders."""

from __future__ import annotations

from decimal import Decimal
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

from apps.library.models import Clip, Video

User = get_user_model()


@pytest.fixture
def storage_root(tmp_path, settings):
    settings.NAKAVID_STORAGE_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def user(db):
    return User.objects.create_user(username="repair", password="secret123!")


def _write(storage_root, relative_path: str, content: bytes = b"bytes") -> None:
    path = storage_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _old_style_video(*, storage_root, user) -> Video:
    _write(storage_root, "originals/2023/09/20230905_quokka_lesson/IMG_7431.MOV")
    _write(storage_root, "originals/2023/09/20230905_quokka_lesson/IMG_7431__web.mp4")
    _write(storage_root, "originals/2023/09/20230905_quokka_lesson/IMG_7431__sheet.jpg")
    video = Video.objects.create(
        title="IMG_7431",
        source_path="/nakavid/originals/2023/09/20230905_quokka_lesson/IMG_7431.MOV",
        playback_path="/nakavid/originals/2023/09/20230905_quokka_lesson/IMG_7431__web.mp4",
        contact_sheet_path="/nakavid/originals/2023/09/20230905_quokka_lesson/IMG_7431__sheet.jpg",
        video_type=Video.VideoType.TYPE_A,
        orientation=Video.Orientation.LANDSCAPE,
        class_name="Quokka",
        theme="Lesson",
        recorded_at=timezone.now(),
        duration_seconds=1036,
        is_private=True,
        created_by=user,
    )
    _write(storage_root, "highlights/2023/09/20230905_quokka_lesson/IMG_7431__clip_001.mp4")
    _write(storage_root, "highlights/2023/09/20230905_quokka_lesson/IMG_7431__clip_001.jpg")
    Clip.objects.create(
        video=video,
        storage_path="/nakavid/highlights/2023/09/20230905_quokka_lesson/IMG_7431__clip_001.mp4",
        thumbnail_path="/nakavid/highlights/2023/09/20230905_quokka_lesson/IMG_7431__clip_001.jpg",
        start_seconds=Decimal("0.000"),
        end_seconds=Decimal("30.000"),
        created_by=user,
    )
    return video


@pytest.mark.django_db
def test_moves_the_originals_files_and_repoints_the_video(storage_root, user):
    video = _old_style_video(storage_root=storage_root, user=user)

    call_command("repair_theme_from_paths", stdout=StringIO())
    video.refresh_from_db()

    assert video.source_path == "/nakavid/originals/2023/09/05/IMG_7431.MOV"
    assert video.playback_path == "/nakavid/originals/2023/09/05/IMG_7431__web.mp4"
    assert video.contact_sheet_path == "/nakavid/originals/2023/09/05/IMG_7431__sheet.jpg"
    assert (storage_root / "originals/2023/09/05/IMG_7431.MOV").is_file()
    assert (storage_root / "originals/2023/09/05/IMG_7431__web.mp4").is_file()
    assert not (storage_root / "originals/2023/09/20230905_quokka_lesson").exists()


@pytest.mark.django_db
def test_moves_the_highlight_clip_and_repoints_it(storage_root, user):
    video = _old_style_video(storage_root=storage_root, user=user)

    call_command("repair_theme_from_paths", stdout=StringIO())

    clip = video.clips.get()
    assert clip.storage_path == "/nakavid/highlights/2023/09/05/IMG_7431__clip_001.mp4"
    assert clip.thumbnail_path == "/nakavid/highlights/2023/09/05/IMG_7431__clip_001.jpg"
    assert (storage_root / "highlights/2023/09/05/IMG_7431__clip_001.mp4").is_file()
    assert not (storage_root / "highlights/2023/09/20230905_quokka_lesson").exists()


@pytest.mark.django_db
def test_a_short_recordings_clip_sharing_the_source_moves_once(storage_root, user):
    """Type B: Clip.storage_path == Video.source_path — one file, not two moves."""
    _write(storage_root, "originals/2026/07/20260701_a_b/IMG_2856.MOV")
    video = Video.objects.create(
        title="IMG_2856",
        source_path="/nakavid/originals/2026/07/20260701_a_b/IMG_2856.MOV",
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

    out = StringIO()
    call_command("repair_theme_from_paths", stdout=out)
    video.refresh_from_db()

    assert video.source_path == "/nakavid/originals/2026/07/01/IMG_2856.MOV"
    assert video.clips.get().storage_path == video.source_path
    assert (storage_root / "originals/2026/07/01/IMG_2856.MOV").is_file()
    assert "moved 1 file(s)" in out.getvalue()


@pytest.mark.django_db
def test_is_idempotent(storage_root, user):
    _old_style_video(storage_root=storage_root, user=user)
    call_command("repair_theme_from_paths", stdout=StringIO())

    second = StringIO()
    call_command("repair_theme_from_paths", stdout=second)

    assert "nothing to move" in second.getvalue()


@pytest.mark.django_db
def test_dry_run_changes_nothing(storage_root, user):
    video = _old_style_video(storage_root=storage_root, user=user)
    out = StringIO()

    call_command("repair_theme_from_paths", "--dry-run", stdout=out)
    video.refresh_from_db()

    assert "would move" in out.getvalue()
    assert video.source_path == "/nakavid/originals/2023/09/20230905_quokka_lesson/IMG_7431.MOV"
    assert (storage_root / "originals/2023/09/20230905_quokka_lesson/IMG_7431.MOV").is_file()


@pytest.mark.django_db
def test_leaves_already_new_style_paths_alone(storage_root, user):
    _write(storage_root, "originals/2026/07/01/already_new.mp4")
    video = Video.objects.create(
        title="already_new",
        source_path="/nakavid/originals/2026/07/01/already_new.mp4",
        video_type=Video.VideoType.TYPE_A,
        orientation=Video.Orientation.LANDSCAPE,
        class_name="A",
        theme="B",
        recorded_at=timezone.now(),
        duration_seconds=60,
        is_private=True,
        created_by=user,
    )

    out = StringIO()
    call_command("repair_theme_from_paths", stdout=out)
    video.refresh_from_db()

    assert "nothing to move" in out.getvalue()
    assert video.source_path == "/nakavid/originals/2026/07/01/already_new.mp4"


@pytest.mark.django_db
def test_repoints_the_db_even_when_the_file_is_missing_on_disk(storage_root, user):
    """The DB row still matters even if the file was lost some other way."""
    video = Video.objects.create(
        title="IMG_7431",
        source_path="/nakavid/originals/2023/09/20230905_quokka_lesson/IMG_7431.MOV",
        video_type=Video.VideoType.TYPE_A,
        orientation=Video.Orientation.LANDSCAPE,
        class_name="Quokka",
        theme="Lesson",
        recorded_at=timezone.now(),
        duration_seconds=1036,
        is_private=True,
        created_by=user,
    )

    err = StringIO()
    call_command("repair_theme_from_paths", stdout=StringIO(), stderr=err)
    video.refresh_from_db()

    assert video.source_path == "/nakavid/originals/2023/09/05/IMG_7431.MOV"
    assert "not on disk" in err.getvalue()
