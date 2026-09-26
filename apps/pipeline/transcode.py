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

# Explicit transpose per clockwise rotation, keyed the same way as
# Video.rotation_degrees/rotation_override_degrees. Applied instead of
# ffmpeg's own autorotate (disabled below with -noautorotate) so the result
# only ever depends on this value, never on ffmpeg's own reading of the
# source's display-matrix side data — confirmed on real footage that the two
# can disagree, and when they do, ffmpeg's guess is not more trustworthy than
# ours; the difference is ours can be corrected (Video.rotation_override_degrees)
# and ffmpeg's can't.
_ROTATION_FILTERS = {
    0: "",
    90: "transpose=1",
    180: "hflip,vflip",
    270: "transpose=2",
}


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


def run_ffmpeg_web_transcode(
    *, source_path: Path, target_path: Path, rotation_degrees: int = 0
) -> None:
    """Re-encode a source into a browser-safe H.264 8-bit 4:2:0 MP4.

    Maps only the first video and (optional) audio streams so iPhone metadata
    tracks (mebx/data) are dropped, forces yuv420p to flatten 10-bit HEVC, and
    writes a faststart MP4 so playback can start before the whole file loads.
    HDR (PQ/HLG) sources are tone-mapped to SDR first — see
    _HDR_TRANSFER_FUNCTIONS.

    rotation_degrees is Video.effective_rotation_degrees — the clockwise
    rotation to bake into the output pixels. ffmpeg's own autorotate is
    disabled (-noautorotate) so this is the only thing that decides rotation;
    passing 0 for a source ffmpeg would otherwise autorotate leaves it as
    coded, which only matters for a source whose metadata is wrong to begin
    with — precisely the case this exists to let someone correct.

    -display_rotation:v 0 matters just as much as the filter above: mapping
    the source stream (-map 0:v:0) carries its display-matrix side data
    through to the output by default, so without this, the output would keep
    declaring the source's *original* rotation on top of pixels this
    function already rotated — confirmed on real footage, every reader that
    respects that tag (every browser, and our own run_ffmpeg_trim /
    run_ffmpeg_thumbnail, neither of which pass -noautorotate) would rotate a
    second time and land on the wrong orientation despite correct pixels.
    """
    target_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-noautorotate",
        "-display_rotation:v",
        "0",
        "-i",
        str(source_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
    ]
    filters = [f for f in (_ROTATION_FILTERS.get(rotation_degrees % 360, ""),) if f]
    if probe_color_transfer(source_path) in _HDR_TRANSFER_FUNCTIONS:
        filters.append(_TONEMAP_FILTER)
    if filters:
        command += ["-vf", ",".join(filters)]
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
