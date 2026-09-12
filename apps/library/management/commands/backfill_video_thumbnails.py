"""Give already-scored long recordings the poster frame they scored before it existed.

ensure_video_thumbnail only runs as part of the score stage, so a Type A
video scored before Video.thumbnail_path existed has no poster and never
will unless something asks for one. Idempotent: a video that already has a
thumbnail is skipped.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.library.models import Video
from apps.pipeline.handlers import ensure_video_thumbnail


class Command(BaseCommand):
    help = "Generate a poster frame for long recordings scored before thumbnails existed."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List what would be thumbnailed without touching disk or the DB.",
        )

    def handle(self, *args, **options) -> None:
        dry_run: bool = options["dry_run"]
        candidates = Video.objects.filter(
            video_type=Video.VideoType.TYPE_A, thumbnail_path=""
        ).exclude(duration_seconds=0)

        done = 0
        for video in candidates:
            self.stdout.write(f"{'would thumbnail' if dry_run else 'thumbnail '} {video.title}")
            if dry_run:
                done += 1
                continue
            try:
                thumbnail_path = ensure_video_thumbnail(video)
            except Exception as exc:  # ffmpeg failures shouldn't stop the rest
                self.stderr.write(f"  failed: {exc}")
                continue
            video.thumbnail_path = thumbnail_path
            video.save(update_fields=["thumbnail_path", "updated_at"])
            done += 1

        verb = "would thumbnail" if dry_run else "thumbnailed"
        self.stdout.write(f"{verb} {done} video(s)")
