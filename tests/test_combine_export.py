from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.library.models import Clip, Combine, CombineClip, Video
from apps.library.storage_paths import build_combine_relative_path, to_absolute_storage_path
from apps.pipeline.combine_export import CombineExportError, run_ffmpeg_concat
from apps.pipeline.handlers import handle_combine_export
from apps.pipeline.models import Job
from apps.pipeline.worker import process_job

User = get_user_model()


@pytest.fixture
def storage_root(tmp_path, settings):
    settings.NAKAVID_STORAGE_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def user(db):
    return User.objects.create_user(username="combine-export", password="secret123!")


def _create_clip(*, storage_root: Path, user, suffix: str) -> Clip:
    recorded_at = timezone.make_aware(datetime(2026, 7, 1, 12, 0))
    video = Video.objects.create(
        title=f"source_{suffix}",
        source_path=to_absolute_storage_path(
            storage_root,
            f"originals/2026/07/20260701_a_animals/source_{suffix}.mp4",
        ),
        video_type=Video.VideoType.TYPE_A,
        orientation=Video.Orientation.LANDSCAPE,
        class_name="A",
        theme="Animals",
        recorded_at=recorded_at,
        duration_seconds=600,
        created_by=user,
    )
    clip_path = to_absolute_storage_path(
        storage_root,
        f"highlights/2026/07/20260701_a_animals/source_{suffix}__clip_001.mp4",
    )
    clip_file = storage_root / clip_path.removeprefix("/nakavid/")
    clip_file.parent.mkdir(parents=True, exist_ok=True)
    clip_file.write_bytes(b"clip-bytes")
    return Clip.objects.create(
        video=video,
        storage_path=clip_path,
        start_seconds=Decimal("10.000"),
        end_seconds=Decimal("40.000"),
        highlight_score=80,
        created_by=user,
    )


def _create_combine_job(*, storage_root: Path, user, clip_ids: list[int]) -> tuple[Combine, Job]:
    clips = [
        _create_clip(storage_root=storage_root, user=user, suffix=str(index)) for index in clip_ids
    ]
    combine = Combine.objects.create(title="Week 1 Highlights", created_by=user)
    for position, clip in enumerate(clips, start=1):
        CombineClip.objects.create(combine=combine, clip=clip, position=position)
    job = Job.objects.create(
        video=clips[0].video,
        combine=combine,
        job_type=Job.JobType.COMBINE_EXPORT,
        status=Job.Status.PROCESSING,
    )
    return combine, job


@pytest.mark.django_db
def test_build_combine_relative_path_uses_title_slug_and_date():
    relative_path = build_combine_relative_path(
        title="Week 1 Highlights",
        created_at=datetime(2026, 7, 8, 15, 30),
    )

    assert relative_path == "combines/week_1_highlights_20260708.mp4"


@pytest.mark.django_db
def test_handle_combine_export_updates_status_and_output_path(storage_root, user):
    combine, job = _create_combine_job(storage_root=storage_root, user=user, clip_ids=[1, 2])
    expected_relative_path = build_combine_relative_path(
        title=combine.title,
        created_at=combine.created_at,
    )
    expected_output_path = to_absolute_storage_path(storage_root, expected_relative_path)

    with patch("apps.pipeline.handlers.run_ffmpeg_concat") as run_concat:
        handle_combine_export(job)

    combine.refresh_from_db()
    assert combine.status == Combine.Status.DONE
    assert combine.output_path == expected_output_path
    run_concat.assert_called_once()
    call_kwargs = run_concat.call_args.kwargs
    assert len(call_kwargs["input_paths"]) == 2
    assert call_kwargs["target_path"] == storage_root / expected_relative_path


@pytest.mark.django_db
def test_handle_combine_export_preserves_clip_order(storage_root, user):
    first = _create_clip(storage_root=storage_root, user=user, suffix="first")
    second = _create_clip(storage_root=storage_root, user=user, suffix="second")
    combine = Combine.objects.create(title="Ordered Combine", created_by=user)
    CombineClip.objects.create(combine=combine, clip=second, position=1)
    CombineClip.objects.create(combine=combine, clip=first, position=2)
    job = Job.objects.create(
        video=second.video,
        combine=combine,
        job_type=Job.JobType.COMBINE_EXPORT,
        status=Job.Status.PROCESSING,
    )

    with patch("apps.pipeline.handlers.run_ffmpeg_concat") as run_concat:
        handle_combine_export(job)

    input_paths = run_concat.call_args.kwargs["input_paths"]
    assert [path.name for path in input_paths] == [
        "source_second__clip_001.mp4",
        "source_first__clip_001.mp4",
    ]


@pytest.mark.django_db
def test_handle_combine_export_sets_error_status_on_ffmpeg_failure(storage_root, user):
    combine, job = _create_combine_job(storage_root=storage_root, user=user, clip_ids=[1])

    with patch(
        "apps.pipeline.handlers.run_ffmpeg_concat",
        side_effect=CombineExportError("ffmpeg concat failed: invalid data"),
    ):
        with pytest.raises(CombineExportError, match="ffmpeg concat failed"):
            handle_combine_export(job)

    combine.refresh_from_db()
    assert combine.status == Combine.Status.ERROR
    assert combine.output_path == ""


@pytest.mark.django_db
def test_process_job_records_stderr_for_combine_export_failure(storage_root, user):
    combine, job = _create_combine_job(storage_root=storage_root, user=user, clip_ids=[1])

    with patch(
        "apps.pipeline.handlers.run_ffmpeg_concat",
        side_effect=CombineExportError("ffmpeg concat failed: invalid data"),
    ):
        process_job(job)

    job.refresh_from_db()
    combine.refresh_from_db()
    assert job.status == Job.Status.ERROR
    assert job.stderr == "ffmpeg concat failed: invalid data"
    assert combine.status == Combine.Status.ERROR


ffmpeg_required = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)


def _make_clip(path, *, seconds, width, height, silent=False):
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={seconds}:size={width}x{height}:rate=25",
    ]
    if not silent:
        command += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if not silent:
        command += ["-c:a", "aac", "-shortest"]
    command += [str(path)]
    subprocess.run(command, check=True, capture_output=True)


def _probe(path):
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,pix_fmt",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    stream = payload["streams"][0]
    return float(payload["format"]["duration"]), stream


@ffmpeg_required
def test_concat_of_mismatched_clips_has_the_right_length_and_codec(tmp_path):
    """The bug: 125s of mixed-shape clips came out as 1h44m of HEVC.

    The concat demuxer with -c copy takes the first input's parameters and
    appends packets that do not match them, producing nonsense rather than an
    error. Portrait and landscape together is the ordinary case here.
    """
    portrait = tmp_path / "portrait.mp4"
    landscape = tmp_path / "landscape.mp4"
    _make_clip(portrait, seconds=3, width=480, height=854)
    _make_clip(landscape, seconds=4, width=854, height=480)
    output = tmp_path / "combined.mp4"

    run_ffmpeg_concat(input_paths=[portrait, landscape], target_path=output)

    duration, stream = _probe(output)
    assert duration == pytest.approx(7.0, abs=1.0)
    assert stream["codec_name"] == "h264"
    assert stream["pix_fmt"] == "yuv420p"
    # One canvas, so the portrait clip is pillarboxed rather than cropped.
    assert (stream["width"], stream["height"]) == (1920, 1080)


@ffmpeg_required
def test_a_silent_clip_does_not_lose_the_audio_of_later_clips(tmp_path):
    """Without a synthesised track, concat drops audio from that point on."""
    silent = tmp_path / "silent.mp4"
    noisy = tmp_path / "noisy.mp4"
    _make_clip(silent, seconds=2, width=640, height=360, silent=True)
    _make_clip(noisy, seconds=2, width=640, height=360)
    output = tmp_path / "combined.mp4"

    run_ffmpeg_concat(input_paths=[silent, noisy], target_path=output)

    streams = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "json",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(streams.stdout).get("streams"), "combine lost its audio track"
    duration, _ = _probe(output)
    assert duration == pytest.approx(4.0, abs=1.0)


@ffmpeg_required
def test_a_broken_input_surfaces_as_an_error(tmp_path):
    """A real ffmpeg failure must raise, not leave a half-written export."""
    not_a_video = tmp_path / "clip.mp4"
    not_a_video.write_bytes(b"this is not a video")
    output = tmp_path / "combined.mp4"

    with pytest.raises(CombineExportError):
        run_ffmpeg_concat(input_paths=[not_a_video], target_path=output)
