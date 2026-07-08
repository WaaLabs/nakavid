from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.library.models import Clip, Video
from apps.library.storage_paths import build_originals_relative_path, to_absolute_storage_path
from apps.pipeline.handlers import handle_probe
from apps.pipeline.models import Job
from apps.pipeline.probe import (
    ProbeError,
    ProbeResult,
    orientation_from_dimensions,
    parse_ffprobe_payload,
    run_ffprobe,
)
from apps.pipeline.worker import process_job

User = get_user_model()

LANDSCAPE_FFPROBE_FIXTURE = {
    "format": {"duration": "125.499"},
    "streams": [
        {
            "codec_type": "video",
            "codec_name": "h264",
            "width": 1920,
            "height": 1080,
        },
        {
            "codec_type": "audio",
            "codec_name": "aac",
        },
    ],
}

PORTRAIT_FFPROBE_FIXTURE = {
    "format": {"duration": "42.1"},
    "streams": [
        {
            "codec_type": "video",
            "codec_name": "hevc",
            "width": 720,
            "height": 1280,
        }
    ],
}


def test_orientation_from_dimensions():
    assert orientation_from_dimensions(width=1920, height=1080) == Video.Orientation.LANDSCAPE
    assert orientation_from_dimensions(width=720, height=1280) == Video.Orientation.PORTRAIT
    assert orientation_from_dimensions(width=1080, height=1080) == Video.Orientation.SQUARE


def test_parse_ffprobe_payload_landscape_fixture():
    result = parse_ffprobe_payload(LANDSCAPE_FFPROBE_FIXTURE)

    assert result == ProbeResult(
        duration_seconds=125,
        orientation=Video.Orientation.LANDSCAPE,
        video_codec="h264",
        width=1920,
        height=1080,
    )


def test_parse_ffprobe_payload_portrait_fixture():
    result = parse_ffprobe_payload(PORTRAIT_FFPROBE_FIXTURE)

    assert result.duration_seconds == 42
    assert result.orientation == Video.Orientation.PORTRAIT
    assert result.video_codec == "hevc"
    assert result.width == 720
    assert result.height == 1280


def test_parse_ffprobe_payload_missing_video_stream():
    with pytest.raises(ProbeError, match="missing a video stream"):
        parse_ffprobe_payload({"format": {"duration": "10"}, "streams": []})


def test_parse_ffprobe_payload_missing_duration():
    with pytest.raises(ProbeError, match="missing format.duration"):
        parse_ffprobe_payload({"format": {}, "streams": LANDSCAPE_FFPROBE_FIXTURE["streams"]})


def test_run_ffprobe_invokes_subprocess(tmp_path):
    video_file = tmp_path / "sample.mp4"
    video_file.write_bytes(b"not-a-real-video")

    completed = type(
        "CompletedProcess",
        (),
        {"stdout": json.dumps(LANDSCAPE_FFPROBE_FIXTURE), "stderr": "", "returncode": 0},
    )()

    with patch("apps.pipeline.probe.subprocess.run", return_value=completed) as run:
        result = run_ffprobe(video_file)

    assert result.duration_seconds == 125
    run.assert_called_once()
    assert run.call_args.args[0][-1] == str(video_file)


def test_run_ffprobe_surfaces_stderr_on_failure(tmp_path):
    video_file = tmp_path / "broken.mp4"
    video_file.write_bytes(b"broken")

    with patch(
        "apps.pipeline.probe.subprocess.run",
        side_effect=__import__("subprocess").CalledProcessError(
            1, "ffprobe", stderr="Invalid data found when processing input"
        ),
    ):
        with pytest.raises(ProbeError, match="Invalid data found"):
            run_ffprobe(video_file)


@pytest.fixture
def storage_root(tmp_path, settings):
    settings.NAKAVID_STORAGE_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def user(db):
    return User.objects.create_user(username="probe-test", password="secret123!")


def _create_video_on_disk(
    *,
    storage_root: Path,
    user,
    video_type: str,
    relative_path: str | None = None,
) -> tuple[Video, Path]:
    if relative_path is None:
        relative_path = build_originals_relative_path(
            recorded_at=timezone.now().date(),
            class_name="A",
            theme="Probe",
            filename="sample.mp4",
        )
    absolute_path = to_absolute_storage_path(storage_root, relative_path)
    file_path = storage_root / relative_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(b"fake-video")

    video = Video.objects.create(
        title="Probe Sample",
        source_path=absolute_path,
        video_type=video_type,
        orientation=Video.Orientation.MIXED,
        class_name="A",
        theme="Probe",
        recorded_at=timezone.now(),
        duration_seconds=1,
        is_private=True,
        created_by=user,
    )
    return video, file_path


@pytest.mark.django_db
def test_handle_probe_persists_metadata(storage_root, user):
    video, _file_path = _create_video_on_disk(
        storage_root=storage_root,
        user=user,
        video_type=Video.VideoType.TYPE_A,
    )
    job = Job.objects.create(video=video, job_type=Job.JobType.PROBE, status=Job.Status.PROCESSING)

    with patch(
        "apps.pipeline.handlers.run_ffprobe",
        return_value=ProbeResult(
            duration_seconds=125,
            orientation=Video.Orientation.LANDSCAPE,
            video_codec="h264",
            width=1920,
            height=1080,
        ),
    ):
        handle_probe(job)

    video.refresh_from_db()
    assert video.duration_seconds == 125
    assert video.orientation == Video.Orientation.LANDSCAPE
    assert video.video_codec == "h264"
    assert video.width == 1920
    assert video.height == 1080


@pytest.mark.django_db
def test_handle_probe_updates_type_b_clip_end(storage_root, user):
    video, _file_path = _create_video_on_disk(
        storage_root=storage_root,
        user=user,
        video_type=Video.VideoType.TYPE_B,
    )
    clip = Clip.objects.create(
        video=video,
        storage_path=video.source_path,
        start_seconds=Decimal("0.000"),
        end_seconds=Decimal("1"),
        created_by=user,
    )
    job = Job.objects.create(video=video, job_type=Job.JobType.PROBE, status=Job.Status.PROCESSING)

    with patch(
        "apps.pipeline.handlers.run_ffprobe",
        return_value=ProbeResult(
            duration_seconds=87,
            orientation=Video.Orientation.PORTRAIT,
            video_codec="hevc",
            width=720,
            height=1280,
        ),
    ):
        handle_probe(job)

    clip.refresh_from_db()
    assert clip.end_seconds == Decimal("87")


@pytest.mark.django_db
def test_process_job_probe_failure_sets_error_stderr(storage_root, user):
    video, _file_path = _create_video_on_disk(
        storage_root=storage_root,
        user=user,
        video_type=Video.VideoType.TYPE_A,
    )
    job = Job.objects.create(
        video=video,
        job_type=Job.JobType.PROBE,
        status=Job.Status.PROCESSING,
        claimed_at=timezone.now(),
    )

    with patch(
        "apps.pipeline.handlers.run_ffprobe",
        side_effect=ProbeError("Invalid data found when processing input"),
    ):
        process_job(job)

    job.refresh_from_db()
    assert job.status == Job.Status.ERROR
    assert job.stderr == "Invalid data found when processing input"
    assert job.finished_at is not None
