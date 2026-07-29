from __future__ import annotations

import subprocess
from pathlib import Path


class TranscodeError(RuntimeError):
    """Raised when the web transcode cannot complete."""


def run_ffmpeg_web_transcode(*, source_path: Path, target_path: Path) -> None:
    """Re-encode a source into a browser-safe H.264 8-bit 4:2:0 MP4.

    Maps only the first video and (optional) audio streams so iPhone metadata
    tracks (mebx/data) are dropped, forces yuv420p to flatten 10-bit HEVC, and
    writes a faststart MP4 so playback can start before the whole file loads.
    """
    target_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-movflags",
        "+faststart",
        str(target_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or "ffmpeg web transcode failed"
        raise TranscodeError(message) from exc
