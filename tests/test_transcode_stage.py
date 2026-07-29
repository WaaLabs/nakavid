from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.library.models import Video
from apps.library.storage_paths import (
    build_originals_relative_path,
    build_playback_relative_path,
    to_absolute_storage_path,
)
from apps.pipeline.handlers import handle_probe, handle_transcode
from apps.pipeline.models import Job
from apps.pipeline.probe import ProbeResult, needs_web_transcode

User = get_user_model()


@pytest.mark.parametrize(
    "codec_name, pixel_format, expected",
    [
        ("h264", "yuv420p", False),
        ("h264", "yuvj420p", False),
        ("h264", "", False),
        ("h264", "yuv420p10le", True),  # 10-bit H.264
        ("h264", "yuv422p", True),
        ("hevc", "yuv420p", True),  # iPhone HEVC 8-bit
        ("hevc", "yuv420p10le", True),  # iPhone HEVC 10-bit
        ("vp9", "yuv420p", True),
    ],
)
def test_needs_web_transcode(codec_name, pixel_format, expected):
    assert needs_web_transcode(codec_name=codec_name, pixel_format=pixel_format) is expected


def test_build_playback_relative_path():
    source = "originals/2026/07/20260729_a_animals/lesson.mov"
    assert (
        build_playback_relative_path(source)
        == "originals/2026/07/20260729_a_animals/lesson__web.mp4"
    )


@pytest.fixture
def storage_root(tmp_path, settings):
    settings.NAKAVID_STORAGE_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def user(db):
    return User.objects.create_user(username="transcode-test", password="secret123!")


def _create_type_a_video(*, storage_root: Path, user, filename: str = "sample.mov") -> Video:
    relative_path = build_originals_relative_path(
        recorded_at=timezone.now().date(),
        class_name="A",
        theme="Transcode",
        filename=filename,
    )
    absolute_path = to_absolute_storage_path(storage_root, relative_path)
    file_path = storage_root / relative_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(b"fake-video")

    return Video.objects.create(
        title="Transcode Sample",
        source_path=absolute_path,
        video_type=Video.VideoType.TYPE_A,
        orientation=Video.Orientation.MIXED,
        class_name="A",
        theme="Transcode",
        recorded_at=timezone.now(),
        duration_seconds=1,
        is_private=True,
        created_by=user,
    )


@pytest.mark.django_db
def test_handle_probe_enqueues_transcode_for_hevc(storage_root, user):
    video = _create_type_a_video(storage_root=storage_root, user=user)
    job = Job.objects.create(video=video, job_type=Job.JobType.PROBE, status=Job.Status.PROCESSING)

    with patch(
        "apps.pipeline.handlers.run_ffprobe",
        return_value=ProbeResult(
            duration_seconds=120,
            orientation=Video.Orientation.LANDSCAPE,
            video_codec="hevc",
            width=1920,
            height=1080,
            pixel_format="yuv420p10le",
        ),
    ):
        handle_probe(job)

    assert video.jobs.filter(job_type=Job.JobType.TRANSCODE).exists()
    assert not video.jobs.filter(job_type=Job.JobType.SCORE).exists()


@pytest.mark.django_db
def test_handle_probe_skips_transcode_for_browser_safe_h264(storage_root, user):
    video = _create_type_a_video(storage_root=storage_root, user=user, filename="sample.mp4")
    job = Job.objects.create(video=video, job_type=Job.JobType.PROBE, status=Job.Status.PROCESSING)

    with patch(
        "apps.pipeline.handlers.run_ffprobe",
        return_value=ProbeResult(
            duration_seconds=120,
            orientation=Video.Orientation.LANDSCAPE,
            video_codec="h264",
            width=1920,
            height=1080,
            pixel_format="yuv420p",
        ),
    ):
        handle_probe(job)

    assert not video.jobs.filter(job_type=Job.JobType.TRANSCODE).exists()
    assert video.jobs.filter(job_type=Job.JobType.SCORE).exists()


@pytest.mark.django_db
def test_handle_transcode_sets_playback_path_and_enqueues_score(storage_root, user):
    video = _create_type_a_video(storage_root=storage_root, user=user)
    job = Job.objects.create(
        video=video, job_type=Job.JobType.TRANSCODE, status=Job.Status.PROCESSING
    )

    with patch("apps.pipeline.handlers.run_ffmpeg_web_transcode") as transcode:
        handle_transcode(job)

    video.refresh_from_db()
    expected_relative = "originals/{}".format(
        video.source_path.split("originals/", 1)[1].rsplit(".", 1)[0]
    )
    assert video.playback_path.endswith("__web.mp4")
    assert video.playback_path == to_absolute_storage_path(
        storage_root, f"{expected_relative}__web.mp4"
    )
    transcode.assert_called_once()
    assert video.jobs.filter(job_type=Job.JobType.SCORE).exists()
