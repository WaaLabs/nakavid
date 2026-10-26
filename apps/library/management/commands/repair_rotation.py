"""Re-read each video's rotation and re-transcode the ones that came out wrong.

apps.pipeline.probe.rotation_degrees used to read a display matrix's rotation
as clockwise; ffprobe reports it counter-clockwise. An iPhone portrait clip
(-90) was stored as 270 instead of 90 and transcoded upside down, and every
clip, thumbnail and still cut from that playback file inherited it. 180 and 0
read the same either way, so only quarter turns were affected.

This re-probes each source with the fixed reader and, where the stored
rotation_degrees differs, saves the corrected value. Only a video whose
stored value is the mirror of the probed one (270 for 90, or 90 for 270) was
rendered by the bug; any other mismatch — a 0 stored by a row that predates
rotation being persisted, say — was transcoded by ffmpeg's own autorotate
back then and already looks right, so it only gets the stored value fixed.
A bug-rendered video with a playback rendition also gets its one-time
thumbnails cleared and a fresh transcode queued — handle_transcode's existing
chain re-cuts clips and stills from the corrected file, same as
backfill_hdr_color. A video still waiting for its
first transcode only needs the stored value fixed: that job reads it when it
runs. A video with rotation_override_degrees set already renders from the
override, so only its stored value is corrected.

Idempotent: once stored values match the source, a re-run changes nothing.
A worker (manage.py run_worker) must be running to process the queued jobs.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.library.models import Video
from apps.pipeline.enqueue import enqueue_transcode_job
from apps.pipeline.models import Job
from apps.pipeline.probe import ProbeError, run_ffprobe


def _file_path(storage_root: Path, storage_path: str) -> Path:
    relative = storage_path.removeprefix("/nakavid/").lstrip("/")
    return storage_root / relative


class Command(BaseCommand):
    help = (
        "Re-read every video's rotation from its source and re-transcode the ones "
        "stored with the wrong rotation (upside-down portrait clips)."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List what would change without touching the DB or the queue.",
        )

    def handle(self, *args, **options) -> None:
        dry_run: bool = options["dry_run"]
        storage_root = Path(settings.NAKAVID_STORAGE_ROOT)

        corrected = requeued = skipped_missing = unreadable = 0
        for video in Video.objects.exclude(source_path="").order_by("id"):
            source_file = _file_path(storage_root, video.source_path)
            if not source_file.exists():
                skipped_missing += 1
                continue
            try:
                probed = run_ffprobe(source_file).rotation
            except ProbeError as exc:
                self.stderr.write(f"  skipping video {video.pk} ({video.title}): {exc}")
                unreadable += 1
                continue
            if probed == video.rotation_degrees:
                continue

            misread = video.rotation_degrees == -probed % 360
            requeue = (
                misread and video.rotation_override_degrees is None and bool(video.playback_path)
            )
            already_queued = video.jobs.filter(
                job_type=Job.JobType.TRANSCODE, status=Job.Status.PENDING
            ).exists()
            action = "re-transcode" if requeue and not already_queued else "fix stored value"
            self.stdout.write(
                f"{'would ' if dry_run else ''}{action}: video {video.pk} ({video.title}) "
                f"{video.rotation_degrees}° -> {probed}°"
            )
            corrected += 1
            if dry_run:
                requeued += requeue and not already_queued
                continue

            with transaction.atomic():
                video.rotation_degrees = probed
                update_fields = ["rotation_degrees", "updated_at"]
                if requeue:
                    if video.thumbnail_path:
                        video.thumbnail_path = ""
                        update_fields.append("thumbnail_path")
                    if video.video_type == Video.VideoType.TYPE_B:
                        clip = video.clips.order_by("id").first()
                        if clip is not None and clip.thumbnail_path:
                            clip.thumbnail_path = ""
                            clip.save(update_fields=["thumbnail_path", "updated_at"])
                video.save(update_fields=update_fields)
                if requeue and not already_queued:
                    enqueue_transcode_job(video=video)
                    requeued += 1

        verb = "would correct" if dry_run else "corrected"
        self.stdout.write(
            f"{verb} {corrected} video(s), {requeued} re-transcode(s) queued; "
            f"{skipped_missing} skipped (source file missing), {unreadable} unreadable"
        )
