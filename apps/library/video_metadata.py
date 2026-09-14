"""Reading a video file's own embedded recording date.

Every ingest path needs a "when was this actually recorded" date, both to
build the storage folder and to populate Video.recorded_at. The video file
itself is the most trustworthy source for that — more so than a hand-typed
date, and more so than a third party's (Immich's) own metadata about the
file, which is itself usually just a read of this same tag.

This does one thing: read the tag, or say there wasn't one. Every caller
already needs its own fallback for "no metadata" (a directory ingest falls
back to the file's mtime; Immich falls back to its own asset metadata; a
fresh upload falls back to today), so no fallback is baked in here.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from django.utils import timezone


def probe_creation_time(file_path: Path) -> datetime | None:
    """The video's own creation_time tag, or None if it has none.

    Never raises: ffprobe missing, the file not being a video ffprobe can
    read, no tag present, or an unparsable tag are all the same case a
    caller already has to handle — there is no recorded date to be had here.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format_tags=creation_time",
                "-of",
                "json",
                str(file_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        tags = (json.loads(result.stdout).get("format") or {}).get("tags") or {}
        raw = tags.get("creation_time")
        if not raw:
            return None
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, UTC)
        return parsed
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError, json.JSONDecodeError):
        return None
