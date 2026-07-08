from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from apps.library.models import Video


class ProbeError(Exception):
    """Raised when ffprobe fails or returns unusable output."""


@dataclass(frozen=True)
class ProbeResult:
    duration_seconds: int
    orientation: str
    video_codec: str
    width: int
    height: int


def orientation_from_dimensions(*, width: int, height: int) -> str:
    if width > height:
        return Video.Orientation.LANDSCAPE
    if height > width:
        return Video.Orientation.PORTRAIT
    return Video.Orientation.SQUARE


def parse_ffprobe_payload(payload: dict) -> ProbeResult:
    format_block = payload.get("format")
    if not format_block or "duration" not in format_block:
        raise ProbeError("ffprobe output missing format.duration")

    streams = payload.get("streams") or []
    video_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "video"),
        None,
    )
    if video_stream is None:
        raise ProbeError("ffprobe output missing a video stream")

    codec_name = video_stream.get("codec_name")
    width = video_stream.get("width")
    height = video_stream.get("height")
    if not codec_name or width is None or height is None:
        raise ProbeError("ffprobe video stream missing codec or dimensions")

    duration = Decimal(str(format_block["duration"]))
    duration_seconds = int(duration.to_integral_value(rounding=ROUND_HALF_UP))
    duration_seconds = max(duration_seconds, 1)

    return ProbeResult(
        duration_seconds=duration_seconds,
        orientation=orientation_from_dimensions(width=int(width), height=int(height)),
        video_codec=str(codec_name),
        width=int(width),
        height=int(height),
    )


def run_ffprobe(file_path: Path) -> ProbeResult:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type,codec_name,width,height",
                "-of",
                "json",
                str(file_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise ProbeError("ffprobe not found on PATH") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise ProbeError(stderr or f"ffprobe exited with code {exc.returncode}") from exc

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError("ffprobe returned invalid JSON") from exc

    return parse_ffprobe_payload(payload)
