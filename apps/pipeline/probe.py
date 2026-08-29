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
    pixel_format: str = ""
    rotation: int = 0


# Codec/pixel-format combinations a browser <video> element can decode directly.
# Anything else (HEVC, VP9-in-mov, 10-bit H.264, 4:2:2/4:4:4, ...) plays audio
# only and must be transcoded to H.264 8-bit 4:2:0 before it can stream.
_BROWSER_SAFE_VIDEO_CODEC = "h264"
_BROWSER_SAFE_PIXEL_FORMATS = frozenset({"", "yuv420p", "yuvj420p"})


def needs_web_transcode(*, codec_name: str, pixel_format: str) -> bool:
    """True when the source stream is not directly playable in a browser."""
    if codec_name != _BROWSER_SAFE_VIDEO_CODEC:
        return True
    return pixel_format not in _BROWSER_SAFE_PIXEL_FORMATS


def orientation_from_dimensions(*, width: int, height: int) -> str:
    if width > height:
        return Video.Orientation.LANDSCAPE
    if height > width:
        return Video.Orientation.PORTRAIT
    return Video.Orientation.SQUARE


def rotation_degrees(video_stream: dict) -> int:
    """Rotation a player will apply, from the stream's display matrix.

    Phones record portrait as a landscape frame plus a rotation matrix, so the
    encoded width and height say nothing about which way up the video is.
    Reading only width/height recorded a portrait clip as landscape.
    """
    for side_data in video_stream.get("side_data_list") or []:
        if "rotation" in side_data:
            try:
                return int(side_data["rotation"]) % 360
            except (TypeError, ValueError):
                continue
    tags = video_stream.get("tags") or {}
    if "rotate" in tags:
        try:
            return int(tags["rotate"]) % 360
        except (TypeError, ValueError):
            return 0
    return 0


def display_dimensions(*, width: int, height: int, rotation: int) -> tuple[int, int]:
    """Dimensions as displayed, with a quarter turn swapping the axes."""
    if rotation % 180 == 90:
        return height, width
    return width, height


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

    rotation = rotation_degrees(video_stream)
    display_width, display_height = display_dimensions(
        width=int(width), height=int(height), rotation=rotation
    )

    return ProbeResult(
        duration_seconds=duration_seconds,
        orientation=orientation_from_dimensions(width=display_width, height=display_height),
        video_codec=str(codec_name),
        width=display_width,
        height=display_height,
        pixel_format=str(video_stream.get("pix_fmt") or ""),
        rotation=rotation,
    )


def run_ffprobe(file_path: Path) -> ProbeResult:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                (
                    "format=duration"
                    ":stream=codec_type,codec_name,width,height,pix_fmt"
                    ":stream_side_data=rotation"
                ),
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
