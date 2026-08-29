"""Concatenate clips into one deterministic export.

Clips do not share a shape. A short recording is portrait 1080x1920 while
extracted highlights are landscape 1920x1080, and codecs differ too. The
concat demuxer with -c copy requires every input to match exactly; given
mismatched inputs it copies the first stream's parameters and appends packets
that do not belong to it, producing a file with the wrong codec and nonsense
timestamps rather than failing. A three-clip, 125 second combine came out as
1h44m of HEVC that way.

So each clip is first normalised to a common canvas, letterboxed rather than
cropped, with audio guaranteed. Only then are they concatenated, which is a
stream copy because by that point they genuinely do match.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

TARGET_WIDTH = 1920
TARGET_HEIGHT = 1080
TARGET_FPS = 30
AUDIO_RATE = 48000


class CombineExportError(RuntimeError):
    """Raised when combine export cannot complete."""


def _has_audio_stream(input_path: Path) -> bool:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=index",
                "-of",
                "json",
                str(input_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise CombineExportError(f"Unable to inspect {input_path.name}: {exc}") from exc
    return bool(json.loads(result.stdout).get("streams"))


def _normalise_clip(*, input_path: Path, target_path: Path) -> None:
    """Re-encode one clip onto the shared canvas, with audio guaranteed.

    Padding rather than cropping: a portrait clip is pillarboxed, not cut down
    to a landscape strip.
    """
    video_filter = (
        f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={TARGET_WIDTH}:{TARGET_HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
        f"setsar=1,fps={TARGET_FPS},format=yuv420p"
    )
    command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(input_path)]

    if _has_audio_stream(input_path):
        command += ["-map", "0:v:0", "-map", "0:a:0"]
    else:
        # A silent clip still needs an audio track, or concat drops the stream
        # for everything after it.
        command += [
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=channel_layout=stereo:sample_rate={AUDIO_RATE}",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-shortest",
        ]

    command += [
        "-vf",
        video_filter,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-ar",
        str(AUDIO_RATE),
        "-ac",
        "2",
        "-video_track_timescale",
        "90000",
        str(target_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or f"ffmpeg could not normalise {input_path.name}"
        raise CombineExportError(message) from exc


def run_ffmpeg_concat(*, input_paths: list[Path], target_path: Path) -> None:
    if not input_paths:
        raise CombineExportError("Combine has no clip files to concat")

    target_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as workspace:
        work = Path(workspace)
        normalised: list[Path] = []
        for index, input_path in enumerate(input_paths):
            part = work / f"part_{index:03d}.mp4"
            _normalise_clip(input_path=input_path, target_path=part)
            normalised.append(part)

        list_path = work / "parts.txt"
        list_path.write_text(
            "".join(
                f"file '{str(part).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n"
                for part in normalised
            ),
            encoding="utf-8",
        )

        command = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            # A copy is safe now: the parts were made to match.
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(target_path),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            message = exc.stderr.strip() or "ffmpeg concat failed"
            raise CombineExportError(message) from exc

    if not target_path.exists():
        raise CombineExportError(f"ffmpeg produced no combine at {target_path}")
