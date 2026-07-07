from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import PurePosixPath

ORIGINALS_PREFIX = "originals"
_PATH_SEGMENT_RE = re.compile(
    r"^originals/(?P<year>\d{4})/(?P<month>\d{2})/"
    r"(?P<date>\d{8})_(?P<class_name>[^/]+)_(?P<theme>[^/]+)/(?P<filename>[^/]+)$"
)


def slug_segment(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def build_originals_relative_path(
    *,
    recorded_at: date | datetime,
    class_name: str,
    theme: str,
    filename: str,
) -> str:
    if isinstance(recorded_at, datetime):
        recorded_at = recorded_at.date()
    date_token = recorded_at.strftime("%Y%m%d")
    class_slug = slug_segment(class_name)
    theme_slug = slug_segment(theme)
    return str(
        PurePosixPath(ORIGINALS_PREFIX)
        / str(recorded_at.year)
        / f"{recorded_at.month:02d}"
        / f"{date_token}_{class_slug}_{theme_slug}"
        / filename
    )


def to_absolute_storage_path(_storage_root, relative_path: str) -> str:
    return f"/nakavid/{relative_path.lstrip('/')}"


@dataclass(frozen=True)
class OriginalsPathMetadata:
    recorded_on: date
    class_name: str
    theme: str
    filename: str


def parse_originals_relative_path(relative_path: str) -> OriginalsPathMetadata:
    match = _PATH_SEGMENT_RE.match(relative_path)
    if match is None:
        raise ValueError(f"Unrecognized originals path: {relative_path}")
    groups = match.groupdict()
    recorded_on = datetime.strptime(groups["date"], "%Y%m%d").date()
    return OriginalsPathMetadata(
        recorded_on=recorded_on,
        class_name=groups["class_name"],
        theme=groups["theme"],
        filename=groups["filename"],
    )
