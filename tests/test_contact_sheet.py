"""The contact sheet is what makes tuning previewable without ffmpeg per request."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from apps.library.storage_paths import build_contact_sheet_relative_path
from apps.pipeline.contact_sheet import (
    COLUMNS,
    MAX_TILES,
    MIN_INTERVAL_SECONDS,
    ContactSheetError,
    plan_contact_sheet,
    run_ffmpeg_contact_sheet,
)

ffmpeg_required = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)


def test_layout_keeps_long_recordings_under_the_tile_cap():
    """A 90 minute lesson must not produce thousands of tiles."""
    layout = plan_contact_sheet(duration_seconds=90 * 60)

    assert layout.tile_count <= MAX_TILES
    assert layout.interval_seconds >= MIN_INTERVAL_SECONDS
    assert layout.columns == COLUMNS
    assert layout.rows * layout.columns >= layout.tile_count


def test_layout_samples_short_recordings_densely():
    layout = plan_contact_sheet(duration_seconds=120)

    assert layout.interval_seconds == MIN_INTERVAL_SECONDS
    assert layout.tile_count == 60


def test_layout_rejects_an_unprobed_video():
    with pytest.raises(ContactSheetError):
        plan_contact_sheet(duration_seconds=0)


def test_contact_sheet_path_sits_beside_its_source():
    assert (
        build_contact_sheet_relative_path("originals/2026/08/20260826_a_b/lesson.mp4")
        == "originals/2026/08/20260826_a_b/lesson__sheet.jpg"
    )


def _write_source(path: Path, seconds: int) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={seconds}:size=320x240:rate=10",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


@ffmpeg_required
def test_contact_sheet_renders_a_single_tiled_image(tmp_path):
    source = tmp_path / "source.mp4"
    _write_source(source, 40)
    layout = plan_contact_sheet(duration_seconds=40)
    target = tmp_path / "nested" / "sheet.jpg"

    run_ffmpeg_contact_sheet(source_path=source, target_path=target, layout=layout)

    assert target.exists()
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            str(target),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    width, height = (int(v) for v in probe.stdout.strip().split(",")[:2])
    # One image holding a grid of tiles, not a single frame.
    assert width == layout.columns * layout.tile_width
    assert height > layout.tile_width


@ffmpeg_required
def test_contact_sheet_reports_a_missing_source(tmp_path):
    layout = plan_contact_sheet(duration_seconds=40)

    with pytest.raises(ContactSheetError):
        run_ffmpeg_contact_sheet(
            source_path=tmp_path / "absent.mp4",
            target_path=tmp_path / "sheet.jpg",
            layout=layout,
        )


@pytest.mark.django_db
def test_handler_records_the_layout_on_the_video(tmp_path, settings):
    """The page needs the layout to map a timestamp onto a tile."""
    from unittest.mock import patch

    from django.contrib.auth import get_user_model
    from django.utils import timezone

    from apps.library.models import Video
    from apps.pipeline.handlers import handle_contact_sheet
    from apps.pipeline.models import Job

    settings.NAKAVID_STORAGE_ROOT = tmp_path
    user = get_user_model().objects.create_user(username="sheet", password="secret123!")
    video = Video.objects.create(
        title="Sheet Sample",
        source_path="/nakavid/originals/2026/08/20260826_a_b/lesson.mp4",
        video_type=Video.VideoType.TYPE_A,
        orientation=Video.Orientation.LANDSCAPE,
        class_name="A",
        theme="B",
        recorded_at=timezone.now(),
        duration_seconds=600,
        is_private=True,
        created_by=user,
    )
    job = Job.objects.create(
        video=video, job_type=Job.JobType.CONTACT_SHEET, status=Job.Status.PROCESSING
    )

    with patch("apps.pipeline.handlers.run_ffmpeg_contact_sheet") as render:
        handle_contact_sheet(job)

    assert render.call_count == 1
    video.refresh_from_db()
    assert video.contact_sheet_path.endswith("lesson__sheet.jpg")
    assert video.contact_sheet_interval_seconds > 0
    assert video.contact_sheet_columns == COLUMNS
    assert video.contact_sheet_tile_count > 0
    assert video.contact_sheet_tile_width > 0


@pytest.mark.django_db
def test_contact_sheet_route_requires_login_and_hands_off_to_the_proxy(client, tmp_path, settings):
    from django.contrib.auth import get_user_model
    from django.urls import reverse
    from django.utils import timezone

    from apps.library.models import Video
    from apps.library.storage_paths import to_accel_redirect_path

    settings.NAKAVID_STORAGE_ROOT = tmp_path
    user = get_user_model().objects.create_user(username="viewer", password="secret123!")
    video = Video.objects.create(
        title="Sheet Sample",
        source_path="/nakavid/originals/2026/08/20260826_a_b/lesson.mp4",
        video_type=Video.VideoType.TYPE_A,
        orientation=Video.Orientation.LANDSCAPE,
        class_name="A",
        theme="B",
        recorded_at=timezone.now(),
        duration_seconds=600,
        is_private=True,
        created_by=user,
    )
    url = reverse("video-contact-sheet", args=[video.pk])

    anonymous = client.get(url)
    assert anonymous.status_code == 302
    assert anonymous["Location"].startswith("/accounts/login/")

    assert client.login(username="viewer", password="secret123!")

    # No sheet rendered yet.
    assert client.get(url).status_code == 404

    video.contact_sheet_path = "/nakavid/originals/2026/08/20260826_a_b/lesson__sheet.jpg"
    video.save(update_fields=["contact_sheet_path"])

    response = client.get(url)
    assert response.status_code == 200
    assert response.content == b""
    assert response["X-Accel-Redirect"] == to_accel_redirect_path(video.contact_sheet_path)
