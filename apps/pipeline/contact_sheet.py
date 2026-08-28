"""Contact sheet: one sprite of evenly spaced frames from a recording.

Scoring tuning needs to show what is on screen at an arbitrary timestamp —
for candidate clips that do not exist yet, under parameters that have not been
run. Generating a frame per request would put ffmpeg in the request path,
which the architecture rules forbid, and generating thousands of loose files
is worse. One sprite per recording, built once by the worker, lets any
timestamp be previewed by offsetting a background image.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

# Enough tiles to see structure, few enough to stay a reasonable image.
MAX_TILES = 240
MIN_INTERVAL_SECONDS = 2
TILE_WIDTH = 160
COLUMNS = 12


class ContactSheetError(RuntimeError):
    """Raised when a contact sheet cannot be generated."""


@dataclass(frozen=True)
class ContactSheetLayout:
    interval_seconds: int
    columns: int
    rows: int
    tile_count: int
    tile_width: int


def plan_contact_sheet(*, duration_seconds: int) -> ContactSheetLayout:
    """Choose a sampling interval that keeps the sheet under MAX_TILES."""
    if duration_seconds <= 0:
        raise ContactSheetError("Video duration must be positive")

    interval = max(MIN_INTERVAL_SECONDS, -(-duration_seconds // MAX_TILES))
    tile_count = max(1, duration_seconds // interval)
    rows = max(1, -(-tile_count // COLUMNS))
    return ContactSheetLayout(
        interval_seconds=interval,
        columns=COLUMNS,
        rows=rows,
        tile_count=tile_count,
        tile_width=TILE_WIDTH,
    )


def run_ffmpeg_contact_sheet(
    *, source_path: Path, target_path: Path, layout: ContactSheetLayout
) -> None:
    """Render the sprite in one pass.

    fps=1/interval samples evenly; tile packs the results into a single image,
    so this is one decode of the file rather than one seek per tile.
    """
    target_path.parent.mkdir(parents=True, exist_ok=True)
    video_filter = (
        f"fps=1/{layout.interval_seconds},"
        f"scale={layout.tile_width}:-2,"
        f"tile={layout.columns}x{layout.rows}"
    )
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source_path),
        "-vf",
        video_filter,
        "-frames:v",
        "1",
        "-q:v",
        "4",
        str(target_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or "ffmpeg contact sheet failed"
        raise ContactSheetError(message) from exc

    if not target_path.exists():
        raise ContactSheetError(f"ffmpeg produced no contact sheet at {target_path}")
