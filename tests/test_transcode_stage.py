from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.library.models import Clip, Still, Video
from apps.library.storage_paths import (
    build_originals_relative_path,
    build_playback_relative_path,
    to_absolute_storage_path,
)
from apps.pipeline.handlers import handle_probe, handle_transcode
from apps.pipeline.models import Job, ScoringParams
from apps.pipeline.probe import ProbeResult, needs_web_transcode
from apps.pipeline.transcode import _TONEMAP_FILTER, run_ffmpeg_web_transcode

User = get_user_model()


def _fake_run(color_transfer: str):
    """A subprocess.run stub: ffprobe calls report color_transfer, ffmpeg calls no-op."""

    def _run(command, **kwargs):
        if command[0] == "ffprobe":
            payload = {"streams": [{"color_transfer": color_transfer}] if color_transfer else []}
            return type("Result", (), {"stdout": json.dumps(payload), "returncode": 0})()
        return type("Result", (), {"stdout": "", "returncode": 0})()

    return _run


def test_run_ffmpeg_web_transcode_tonemaps_hdr_source(tmp_path):
    source = tmp_path / "source.mov"
    source.write_bytes(b"fake")
    target = tmp_path / "out" / "source__web.mp4"

    with patch("apps.pipeline.transcode.subprocess.run", side_effect=_fake_run("smpte2084")) as run:
        run_ffmpeg_web_transcode(source_path=source, target_path=target)

    ffmpeg_call = run.call_args_list[-1]
    command = ffmpeg_call.args[0] if ffmpeg_call.args else ffmpeg_call.kwargs["command"]
    assert "-vf" in command
    assert command[command.index("-vf") + 1] == _TONEMAP_FILTER


def test_run_ffmpeg_web_transcode_leaves_sdr_source_untouched(tmp_path):
    source = tmp_path / "source.mov"
    source.write_bytes(b"fake")
    target = tmp_path / "out" / "source__web.mp4"

    with patch("apps.pipeline.transcode.subprocess.run", side_effect=_fake_run("bt709")) as run:
        run_ffmpeg_web_transcode(source_path=source, target_path=target)

    ffmpeg_call = run.call_args_list[-1]
    command = ffmpeg_call.args[0] if ffmpeg_call.args else ffmpeg_call.kwargs["command"]
    assert "-vf" not in command


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


@pytest.mark.django_db
def test_handle_transcode_refreshes_clips_and_stills_from_other_scoring_params(
    storage_root, user
):
    """A re-transcode replaces the one file every clip/still is cut from.

    Only the active ScoringParams row's output gets refreshed through the
    normal score -> extraction chain (enqueue_score_job always scores with
    the active row). Any other row that already has clips/stills here —
    e.g. a fixed/variable comparison, or a row that used to be active before
    a newer one took over — needs its own re-queue, or it keeps pointing at
    whatever the previous (possibly washed-out or wrongly oriented) rendition
    looked like. See backfill_hdr_color, which surfaced this.
    """
    video = _create_type_a_video(storage_root=storage_root, user=user)
    stale_params = ScoringParams.objects.create()
    active_params = ScoringParams.objects.create()
    assert active_params.pk > stale_params.pk

    Clip.objects.create(
        video=video,
        storage_path="/nakavid/highlights/2026/07/sample/sample__clip_001.mp4",
        start_seconds=0,
        end_seconds=4,
        scoring_params=stale_params,
        created_by=user,
    )
    Still.objects.create(
        video=video,
        storage_path="/nakavid/highlights/2026/07/sample/sample__still_001__p{}.jpg".format(
            stale_params.pk
        ),
        capture_seconds=0,
        scoring_params=stale_params,
        created_by=user,
    )
    job = Job.objects.create(
        video=video, job_type=Job.JobType.TRANSCODE, status=Job.Status.PROCESSING
    )

    with patch("apps.pipeline.handlers.run_ffmpeg_web_transcode"):
        handle_transcode(job)

    stale_clip_jobs = video.jobs.filter(
        job_type=Job.JobType.CLIP_EXTRACTION, scoring_params=stale_params
    )
    stale_still_jobs = video.jobs.filter(
        job_type=Job.JobType.STILL_EXTRACTION, scoring_params=stale_params
    )
    assert stale_clip_jobs.exists()
    assert stale_still_jobs.exists()
    # The active row's clips/stills come from the score stage this job
    # queued, not a direct re-queue here — no double-processing.
    assert not video.jobs.filter(
        job_type=Job.JobType.CLIP_EXTRACTION, scoring_params=active_params
    ).exists()
    assert not video.jobs.filter(
        job_type=Job.JobType.STILL_EXTRACTION, scoring_params=active_params
    ).exists()


@pytest.mark.django_db
def test_handle_transcode_skips_refresh_when_only_active_scoring_params_has_output(
    storage_root, user
):
    video = _create_type_a_video(storage_root=storage_root, user=user)
    active_params = ScoringParams.objects.create()
    Clip.objects.create(
        video=video,
        storage_path="/nakavid/highlights/2026/07/sample/sample__clip_001.mp4",
        start_seconds=0,
        end_seconds=4,
        scoring_params=active_params,
        created_by=user,
    )
    job = Job.objects.create(
        video=video, job_type=Job.JobType.TRANSCODE, status=Job.Status.PROCESSING
    )

    with patch("apps.pipeline.handlers.run_ffmpeg_web_transcode"):
        handle_transcode(job)

    assert not video.jobs.filter(job_type=Job.JobType.CLIP_EXTRACTION).exists()
    assert not video.jobs.filter(job_type=Job.JobType.STILL_EXTRACTION).exists()
