"""Ingest a folder of existing footage without going through the browser.

The upload form is fine for one clip off a phone. It is the wrong tool for an
archive that already exists on disk, and for long recordings it means pushing
gigabytes through a browser to a machine that can already see the file.

Recording dates come from each file's own metadata rather than from a flag, so
a folder spanning several days lands in the right places.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.text import get_valid_filename

from apps.library.models import Clip, Video
from apps.library.storage_paths import (
    build_originals_relative_path,
    to_absolute_storage_path,
)
from apps.pipeline.enqueue import STUB_DURATION_SECONDS, enqueue_probe_job

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".mts", ".m2ts", ".webm"}


def recorded_at_for(file_path: Path) -> datetime:
    """The moment the camera recorded this, falling back to the file's mtime.

    A directory of footage usually spans days, so taking the date from each
    file beats one flag applied to everything.
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
        if raw:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed, UTC)
            return parsed
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError, json.JSONDecodeError):
        pass

    stamp = datetime.fromtimestamp(file_path.stat().st_mtime, tz=UTC)
    return stamp


class Command(BaseCommand):
    help = "Ingest every video in a directory into the library."

    def add_arguments(self, parser) -> None:
        parser.add_argument("directory", type=Path)
        parser.add_argument("--class-name", required=True)
        parser.add_argument("--theme", required=True)
        parser.add_argument(
            "--type",
            choices=["long", "short"],
            default="long",
            help="long recordings are split into clips; short ones are used as they are",
        )
        parser.add_argument(
            "--user",
            help="Username to attribute the ingest to. Defaults to the only superuser.",
        )
        parser.add_argument(
            "--recursive", action="store_true", help="Descend into sub-directories."
        )
        parser.add_argument(
            "--move",
            action="store_true",
            help="Move files into the library instead of copying them.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be ingested without copying or writing rows.",
        )

    def _resolve_user(self, username: str | None):
        model = get_user_model()
        if username:
            try:
                return model.objects.get(username=username)
            except model.DoesNotExist as exc:
                raise CommandError(f"No user named {username!r}") from exc
        superusers = list(model.objects.filter(is_superuser=True).order_by("id")[:2])
        if len(superusers) == 1:
            return superusers[0]
        raise CommandError("Pass --user: there is not exactly one superuser to attribute this to")

    def handle(self, *args, **options) -> None:
        directory: Path = options["directory"]
        if not directory.is_dir():
            raise CommandError(f"Not a directory: {directory}")

        dry_run: bool = options["dry_run"]
        user = self._resolve_user(options["user"])
        video_type = Video.VideoType.TYPE_A if options["type"] == "long" else Video.VideoType.TYPE_B
        orientation = (
            Video.Orientation.LANDSCAPE
            if video_type == Video.VideoType.TYPE_A
            else Video.Orientation.MIXED
        )
        storage_root = Path(settings.NAKAVID_STORAGE_ROOT)

        pattern = "**/*" if options["recursive"] else "*"
        candidates = sorted(
            path
            for path in directory.glob(pattern)
            if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES
        )
        if not candidates:
            self.stdout.write("No video files found.")
            return

        ingested = skipped = 0
        for source_file in candidates:
            recorded_at = recorded_at_for(source_file)
            relative_path = build_originals_relative_path(
                recorded_at=recorded_at,
                class_name=options["class_name"],
                theme=options["theme"],
                filename=get_valid_filename(source_file.name),
            )
            storage_path = to_absolute_storage_path(storage_root, relative_path)

            if Video.objects.filter(source_path=storage_path).exists():
                self.stdout.write(f"skip  {source_file.name} — already in the library")
                skipped += 1
                continue

            self.stdout.write(
                f"{'would add' if dry_run else 'add '} {source_file.name} "
                f"({recorded_at.date()}) -> {relative_path}"
            )
            if dry_run:
                ingested += 1
                continue

            destination = storage_root / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            if options["move"]:
                shutil.move(str(source_file), destination)
            else:
                shutil.copy2(source_file, destination)

            with transaction.atomic():
                video = Video.objects.create(
                    title=source_file.stem,
                    source_path=storage_path,
                    video_type=video_type,
                    orientation=orientation,
                    class_name=options["class_name"],
                    theme=options["theme"],
                    recorded_at=recorded_at,
                    # Probe replaces this; it cannot be zero because the column
                    # is positive and the real value needs ffprobe.
                    duration_seconds=STUB_DURATION_SECONDS,
                    is_private=True,
                    created_by=user,
                )
                if video_type == Video.VideoType.TYPE_B:
                    Clip.objects.create(
                        video=video,
                        storage_path=storage_path,
                        start_seconds=0,
                        end_seconds=STUB_DURATION_SECONDS,
                        created_by=user,
                    )
                enqueue_probe_job(video=video)
            ingested += 1

        verb = "would ingest" if dry_run else "ingested"
        self.stdout.write(f"{verb} {ingested} file(s), skipped {skipped} already present")
