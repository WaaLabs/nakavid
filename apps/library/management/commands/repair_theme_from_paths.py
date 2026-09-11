"""Move already-ingested files off the old {date}_{class}_{theme} folder.

Class and theme are DB metadata now — see AGENTS.md's storage path
convention — with folders keyed on the recording date alone. Anything
ingested before that change still sits on disk under the old folder name,
with DB rows pointing at it. This walks every Video and Clip path field,
and for any that still match the old shape, moves the file on disk and
repoints the row at the new one.

Idempotent: a path that doesn't match the old shape is left alone, and a
file already moved by an earlier run is detected and only the DB repoint
(if still pending) runs again.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.library.models import Clip, Video

_OLD_DAY_FOLDER_RE = re.compile(
    r"^(?P<prefix>.+/(?:originals|highlights))/(?P<year>\d{4})/(?P<month>\d{2})/"
    r"(?P<yyyymmdd>\d{8})_[^/]+_[^/]+/(?P<rest>.+)$"
)


def _rewritten(old_path: str) -> str | None:
    """The new path for an old-style one, or None if it's already new-style."""
    match = _OLD_DAY_FOLDER_RE.match(old_path)
    if match is None:
        return None
    day = match.group("yyyymmdd")[6:8]
    return (
        f"{match.group('prefix')}/{match.group('year')}/{match.group('month')}/"
        f"{day}/{match.group('rest')}"
    )


_VIDEO_PATH_FIELDS = ("source_path", "playback_path", "contact_sheet_path")
_CLIP_PATH_FIELDS = ("storage_path", "thumbnail_path")


class Command(BaseCommand):
    help = "Move files off the old {date}_{class}_{theme} originals/highlights folder."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would move without touching disk or the DB.",
        )

    def handle(self, *args, **options) -> None:
        dry_run: bool = options["dry_run"]
        storage_root = Path(settings.NAKAVID_STORAGE_ROOT)

        renames: dict[str, str] = {}
        for video in Video.objects.all():
            for field in _VIDEO_PATH_FIELDS:
                value = getattr(video, field)
                new_value = _rewritten(value) if value else None
                if new_value:
                    renames.setdefault(value, new_value)
        for clip in Clip.objects.all():
            for field in _CLIP_PATH_FIELDS:
                value = getattr(clip, field)
                new_value = _rewritten(value) if value else None
                if new_value:
                    renames.setdefault(value, new_value)

        if not renames:
            self.stdout.write("nothing to move")
            return

        for old_db_path, new_db_path in renames.items():
            self.stdout.write(
                f"{'would move' if dry_run else 'move '} {old_db_path} -> {new_db_path}"
            )
        if dry_run:
            self.stdout.write(f"would move {len(renames)} file(s)")
            return

        moved = 0
        for old_db_path, new_db_path in renames.items():
            old_file = storage_root / old_db_path.removeprefix("/nakavid/")
            new_file = storage_root / new_db_path.removeprefix("/nakavid/")
            if new_file.exists():
                continue  # a previous run already moved this one
            if not old_file.is_file():
                self.stderr.write(f"  not on disk, repointing the DB only: {old_file}")
                continue
            new_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_file), str(new_file))
            moved += 1

        for field in _VIDEO_PATH_FIELDS:
            for old_db_path, new_db_path in renames.items():
                Video.objects.filter(**{field: old_db_path}).update(**{field: new_db_path})
        for field in _CLIP_PATH_FIELDS:
            for old_db_path, new_db_path in renames.items():
                Clip.objects.filter(**{field: old_db_path}).update(**{field: new_db_path})

        # Best-effort: drop the old day folder once it's empty.
        for old_db_path in renames:
            old_dir = (storage_root / old_db_path.removeprefix("/nakavid/")).parent
            try:
                old_dir.rmdir()
            except OSError:
                pass

        self.stdout.write(f"moved {moved} file(s), repointed {len(renames)} path(s)")
