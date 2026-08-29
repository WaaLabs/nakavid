"""Point already-transcoded short recordings at their playable rendition.

The fix in #59 applies when a transcode runs. Short recordings transcoded
before it kept a clip pointing at the original source, so they still stream
the file the transcode existed to replace. Idempotent: running it twice
changes nothing the second time.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.library.models import Video


class Command(BaseCommand):
    help = "Repoint short-recording clips at their transcoded rendition."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything.",
        )

    def handle(self, *args, **options) -> None:
        dry_run: bool = options["dry_run"]
        candidates = (
            Video.objects.filter(video_type=Video.VideoType.TYPE_B)
            .exclude(playback_path="")
            .prefetch_related("clips")
        )

        repaired = 0
        for video in candidates:
            for clip in video.clips.all():
                if clip.storage_path != video.source_path:
                    continue
                self.stdout.write(
                    f"clip {clip.pk} ({video.title}): "
                    f"{clip.storage_path.rsplit('/', 1)[-1]} -> "
                    f"{video.playback_path.rsplit('/', 1)[-1]}"
                )
                if not dry_run:
                    clip.storage_path = video.playback_path
                    clip.save(update_fields=["storage_path", "updated_at"])
                repaired += 1

        verb = "would repoint" if dry_run else "repointed"
        self.stdout.write(f"{verb} {repaired} clip(s)")
