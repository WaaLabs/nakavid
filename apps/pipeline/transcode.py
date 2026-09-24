from __future__ import annotations

import json
import subprocess
from pathlib import Path


class TranscodeError(RuntimeError):
    """Raised when the web transcode cannot complete."""


# PQ (HDR10/Dolby Vision) and HLG, the two transfer curves phones tag HDR
# footage with. Flattening these to 8-bit with a plain -pix_fmt yuv420p
# leaves the PQ/HLG-curved sample values in place but drops the tag a player
# would need to decode them correctly, so downstream (playback, and every
# clip/thumbnail/still cut from it) gets decoded as if it were BT.709 gamma
# and comes out flat and washed out.
_HDR_TRANSFER_FUNCTIONS = frozenset({"smpte2084", "arib-std-b67"})

# Tone-maps PQ/HLG down to SDR BT.709 before the encoder flattens it to 8-bit,
# rather than truncating a curve that was never gamma in the first place.
_TONEMAP_FILTER = (
    "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
    "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
)


def probe_color_transfer(source_path: Path) -> str:
    """The source's tagged color transfer function, or "" if unreadable.

    Public so a backfill can decide which already-processed videos need
    re-transcoding without duplicating this probe.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=color_transfer",
                "-of",
                "json",
                str(source_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""
    try:
        streams = json.loads(result.stdout).get("streams") or []
    except json.JSONDecodeError:
        return ""
    if not streams:
        return ""
    return str(streams[0].get("color_transfer") or "")


def run_ffmpeg_web_transcode(*, source_path: Path, target_path: Path) -> None:
    """Re-encode a source into a browser-safe H.264 8-bit 4:2:0 MP4.

    Maps only the first video and (optional) audio streams so iPhone metadata
    tracks (mebx/data) are dropped, forces yuv420p to flatten 10-bit HEVC, and
    writes a faststart MP4 so playback can start before the whole file loads.
    HDR (PQ/HLG) sources are tone-mapped to SDR first — see
    _HDR_TRANSFER_FUNCTIONS.
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
    ]
    if probe_color_transfer(source_path) in _HDR_TRANSFER_FUNCTIONS:
        command += ["-vf", _TONEMAP_FILTER]
    command += [
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
