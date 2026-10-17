"""Give already-imported short recordings the stills pass long recordings get.

Short recordings (Video.VideoType.TYPE_B) only started getting
STILL_EXTRACTION jobs once handle_score's short-recording branch grew an
enqueue_still_extraction_job call (see apps.pipeline.handlers.handle_score);
anything imported and scored before that landed has no Still rows at all.

This finds already-probed short recordings (width is only ever set by
handle_probe, for either video type) with no still for the active
ScoringParams row, and queues a STILL_EXTRACTION job for each —
handle_still_extraction does the rest, reading straight from the video's own
file rather than from any prior extraction. A worker (manage.py run_worker)
must be running to actually process the queued jobs. Idempotent: a video
that already has a still for the active row is skipped.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.library.models import Video
from apps.pipeline.enqueue import enqueue_still_extraction_job
from apps.pipeline.scoring import get_active_scoring_params


class Command(BaseCommand):
    help = "Queue a stills pass for already-imported short recordings that have none yet."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List what would be queued without touching the DB or the queue.",
        )
        parser.add_argument("--limit", type=int, default=0, help="Stop after this many videos.")

    def handle(self, *args, **options) -> None:
        dry_run: bool = options["dry_run"]
        limit: int = options["limit"]
        active_params = get_active_scoring_params()

        candidates = (
            Video.objects.filter(video_type=Video.VideoType.TYPE_B, width__isnull=False)
            .exclude(stills__scoring_params_id=active_params.pk)
            .order_by("id")
        )

        queued = 0
        for video in candidates:
            if limit and queued >= limit:
                break
            self.stdout.write(f"{'would queue' if dry_run else 'queuing '} {video.title}")
            if not dry_run:
                enqueue_still_extraction_job(video=video, scoring_params_id=active_params.pk)
            queued += 1

        verb = "would queue" if dry_run else "queued"
        self.stdout.write(f"{verb} stills for {queued} short recording(s)")
        if queued and not dry_run:
            self.stdout.write(
                "Run `manage.py run_worker` if one isn't already running to process the queue."
            )
