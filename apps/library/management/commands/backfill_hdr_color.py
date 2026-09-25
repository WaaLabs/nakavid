"""Re-queue already-processed HDR videos so their washed-out output gets fixed.

apps.pipeline.transcode.run_ffmpeg_web_transcode now tone-maps PQ/HLG (HDR)
sources to SDR before flattening to 8-bit; videos transcoded before that fix
landed have a washed-out playback file, and every clip, clip thumbnail, still
and poster frame cut from it inherited the same flatness (see
_playback_file_path in apps.pipeline.handlers — everything downstream reads
from that one file).

This finds videos whose original source is HDR and already has a playback
rendition, clears the fields that gate one-time generation (Video.thumbnail_path,
and a short recording's own Clip.thumbnail_path) so they regenerate, and
queues a fresh transcode job. handle_transcode's existing chain
(transcode -> contact sheet + score -> clip/still extraction) does the rest,
using each video's currently active ScoringParams — the same thing a fresh
ingest of that file would produce today. handle_transcode also re-cuts
clips/stills for any *other* ScoringParams row that already has output for
that video (a fixed/variable comparison, or last month's active row before a
newer one took over) — those were cut from the same now-replaced playback
file and would otherwise keep the pre-fix, washed-out (and, for a portrait
or upside-down source, wrongly oriented) render. A worker (manage.py
run_worker) must be running to actually process the queued jobs.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.library.models import Video
from apps.pipeline.enqueue import enqueue_transcode_job
from apps.pipeline.transcode import probe_color_transfer


def _file_path(storage_root: Path, storage_path: str) -> Path:
    relative = storage_path.removeprefix("/nakavid/").lstrip("/")
    return storage_root / relative


class Command(BaseCommand):
    help = (
        "Re-queue videos whose HDR source was transcoded before tone mapping "
        "existed, so their playback file, clips, thumbnails and stills "
        "regenerate without the washed-out colors."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List what would be re-queued without touching disk, the DB, or the queue.",
        )

    def handle(self, *args, **options) -> None:
        dry_run: bool = options["dry_run"]
        storage_root = Path(settings.NAKAVID_STORAGE_ROOT)

        candidates = Video.objects.exclude(source_path="").order_by("id")

        queued = 0
        skipped_no_playback = 0
        skipped_missing_file = 0

        for video in candidates:
            source_file = _file_path(storage_root, video.source_path)
            if not source_file.exists():
                skipped_missing_file += 1
                continue

            transfer = probe_color_transfer(source_file)
            if transfer not in {"smpte2084", "arib-std-b67"}:
                continue

            if not video.playback_path:
                # HDR-tagged but never flagged as needing a web transcode (an
                # 8-bit H.264 HLG source, in practice) — nothing to re-run yet.
                self.stderr.write(
                    f"  skipping video {video.pk} ({video.title}): HDR source "
                    f"({transfer}) has no playback rendition to fix"
                )
                skipped_no_playback += 1
                continue

            self.stdout.write(
                f"{'would re-queue' if dry_run else 're-queuing '} video {video.pk} "
                f"({video.title}) [{transfer}]"
            )
            if dry_run:
                queued += 1
                continue

            with transaction.atomic():
                if video.thumbnail_path:
                    video.thumbnail_path = ""
                    video.save(update_fields=["thumbnail_path", "updated_at"])
                if video.video_type == Video.VideoType.TYPE_B:
                    clip = video.clips.order_by("id").first()
                    if clip is not None and clip.thumbnail_path:
                        clip.thumbnail_path = ""
                        clip.save(update_fields=["thumbnail_path", "updated_at"])
                enqueue_transcode_job(video=video)
            queued += 1

        verb = "would re-queue" if dry_run else "re-queued"
        self.stdout.write(
            f"{verb} {queued} video(s); "
            f"{skipped_no_playback} HDR video(s) skipped (no playback rendition); "
            f"{skipped_missing_file} video(s) skipped (source file missing)"
        )
        if queued and not dry_run:
            self.stdout.write(
                "Run `manage.py run_worker` if one isn't already running to process the queue."
            )
