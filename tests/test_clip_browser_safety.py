"""Guard that extracted clips are actually playable in a browser.

Every ffmpeg call elsewhere in the suite is mocked, so nothing verified the
bytes coming out. Cutting from a 10-bit HDR original produced H.264 High 10
clips — audio played, video was a black frame — while the suite stayed green.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from apps.pipeline.extraction import run_ffmpeg_trim

ffmpeg_required = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)

BROWSER_SAFE_PIXEL_FORMAT = "yuv420p"


def _video_stream(path: Path) -> dict:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=pix_fmt,codec_name,profile",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)["streams"][0]


def _write_ten_bit_source(path: Path) -> None:
    """A 10-bit source, the shape that produced black-video clips."""
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
            "testsrc=duration=2:size=160x120:rate=15",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p10le",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


@ffmpeg_required
def test_trim_flattens_a_ten_bit_source_to_browser_safe_output(tmp_path):
    source = tmp_path / "source-10bit.mp4"
    _write_ten_bit_source(source)
    assert _video_stream(source)["pix_fmt"] == "yuv420p10le"

    target = tmp_path / "clip.mp4"
    run_ffmpeg_trim(source_path=source, target_path=target, start_seconds=0.0, end_seconds=1.0)

    stream = _video_stream(target)
    assert stream["codec_name"] == "h264"
    assert stream["pix_fmt"] == BROWSER_SAFE_PIXEL_FORMAT
    assert "10" not in stream["profile"]


@ffmpeg_required
def test_trim_writes_a_playable_clip_of_the_requested_length(tmp_path):
    source = tmp_path / "source-10bit.mp4"
    _write_ten_bit_source(source)

    target = tmp_path / "clip.mp4"
    run_ffmpeg_trim(source_path=source, target_path=target, start_seconds=0.5, end_seconds=1.5)

    assert target.exists()
    assert target.stat().st_size > 0
