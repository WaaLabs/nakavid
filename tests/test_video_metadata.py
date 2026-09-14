"""Reading a video's own recording date, straight from its metadata."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from apps.library.video_metadata import probe_creation_time

ffmpeg_required = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available")


def _write_video(path: Path, *, creation_time: str | None = None, seconds: int = 1) -> None:
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={seconds}:size=160x120:rate=10",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
    ]
    if creation_time:
        command += ["-metadata", f"creation_time={creation_time}"]
    command.append(str(path))
    subprocess.run(command, check=True, capture_output=True)


@ffmpeg_required
def test_reads_the_files_own_creation_time_tag(tmp_path):
    video = tmp_path / "tagged.mp4"
    _write_video(video, creation_time="2019-03-04T10:15:00Z")

    probed = probe_creation_time(video)

    assert probed is not None
    assert probed.isoformat() == "2019-03-04T10:15:00+00:00"


@ffmpeg_required
def test_returns_none_when_the_file_has_no_creation_time_tag(tmp_path):
    video = tmp_path / "untagged.mp4"
    _write_video(video)

    assert probe_creation_time(video) is None


def test_returns_none_for_a_file_that_is_not_a_video(tmp_path):
    not_a_video = tmp_path / "notes.txt"
    not_a_video.write_text("not a video")

    assert probe_creation_time(not_a_video) is None


def test_returns_none_for_a_missing_file(tmp_path):
    assert probe_creation_time(tmp_path / "does_not_exist.mp4") is None
