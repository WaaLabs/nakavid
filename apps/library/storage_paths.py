from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import PurePosixPath

ORIGINALS_PREFIX = "originals"
HIGHLIGHTS_PREFIX = "highlights"
COMBINES_PREFIX = "combines"
STAGING_PREFIX = ".staging"
_PATH_SEGMENT_RE = re.compile(
    r"^originals/(?P<year>\d{4})/(?P<month>\d{2})/(?P<day>\d{2})/(?P<filename>[^/]+)$"
)


def slug_segment(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def build_originals_relative_path(
    *,
    recorded_at: date | datetime,
    filename: str,
) -> str:
    """originals/{year}/{month}/{day}/{filename}.

    Class and theme are not encoded here — they're Video fields, nothing
    more. A folder keyed only on the recording date means two videos from
    the same day never collide on class or theme spelling, and renaming a
    class or theme later is a DB write, not a filesystem operation.
    """
    if isinstance(recorded_at, datetime):
        recorded_at = recorded_at.date()
    return str(
        PurePosixPath(ORIGINALS_PREFIX)
        / str(recorded_at.year)
        / f"{recorded_at.month:02d}"
        / f"{recorded_at.day:02d}"
        / filename
    )


def build_staging_relative_path(token: str, filename: str) -> str:
    """A scratch location for a file whose dated folder isn't known yet.

    A video's recording date is read from the file itself, which means the
    file has to exist before the date does — the opposite order every other
    build_* path assumes. Land it here first, under a token unique to the
    ingest (an Immich asset id, an upload id, a uuid), then move it once the
    real date is known. Outside originals/ and highlights/ so nothing scans
    or serves a file mid-ingest.
    """
    return str(PurePosixPath(STAGING_PREFIX) / token / filename)


def build_highlight_relative_paths(
    *,
    recorded_at: date | datetime,
    source_stem: str,
    clip_index: int,
) -> tuple[str, str]:
    """highlights/{year}/{month}/{day}/{stem}__clip_{NNN}.{mp4,jpg} — see
    build_originals_relative_path for why class/theme aren't in the path."""
    if isinstance(recorded_at, datetime):
        recorded_at = recorded_at.date()
    clip_name = f"{source_stem}__clip_{clip_index:03d}"
    base = (
        PurePosixPath(HIGHLIGHTS_PREFIX)
        / str(recorded_at.year)
        / f"{recorded_at.month:02d}"
        / f"{recorded_at.day:02d}"
    )
    return str(base / f"{clip_name}.mp4"), str(base / f"{clip_name}.jpg")


def build_contact_sheet_relative_path(source_relative_path: str) -> str:
    """Sibling contact-sheet sprite path, alongside the source it samples."""
    source = PurePosixPath(source_relative_path)
    return str(source.with_name(f"{source.stem}__sheet.jpg"))


def build_video_thumbnail_relative_path(source_relative_path: str) -> str:
    """A video's own poster frame, beside the file it came from.

    Used both for a short recording (its single clip is the whole video) and
    a long recording's browse-page card (one per video, not per clip).
    """
    source = PurePosixPath(source_relative_path)
    return str(source.with_name(f"{source.stem}__thumb.jpg"))


def build_playback_relative_path(source_relative_path: str) -> str:
    """Sibling H.264 rendition path for a source that is not browser-playable."""
    source = PurePosixPath(source_relative_path)
    return str(source.with_name(f"{source.stem}__web.mp4"))


def build_combine_relative_path(*, title: str, created_at: date | datetime) -> str:
    if isinstance(created_at, datetime):
        created_at = created_at.date()
    title_slug = slug_segment(title)
    date_token = created_at.strftime("%Y%m%d")
    return str(PurePosixPath(COMBINES_PREFIX) / f"{title_slug}_{date_token}.mp4")


def to_absolute_storage_path(_storage_root, relative_path: str) -> str:
    return f"/nakavid/{relative_path.lstrip('/')}"


def to_accel_redirect_path(absolute_storage_path: str) -> str:
    """Path for Caddy X-Accel-Redirect (relative to /srv/nakavid root)."""
    normalized = absolute_storage_path.strip()
    if normalized.startswith("/nakavid/"):
        return normalized[len("/nakavid") :]
    if normalized.startswith("/"):
        return normalized
    return f"/{normalized}"


@dataclass(frozen=True)
class OriginalsPathMetadata:
    recorded_on: date
    filename: str


def parse_originals_relative_path(relative_path: str) -> OriginalsPathMetadata:
    match = _PATH_SEGMENT_RE.match(relative_path)
    if match is None:
        raise ValueError(f"Unrecognized originals path: {relative_path}")
    groups = match.groupdict()
    recorded_on = date(int(groups["year"]), int(groups["month"]), int(groups["day"]))
    return OriginalsPathMetadata(recorded_on=recorded_on, filename=groups["filename"])
