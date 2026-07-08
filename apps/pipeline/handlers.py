from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.db import transaction

from apps.library.models import Clip, Video
from apps.pipeline.enqueue import enqueue_score_job
from apps.pipeline.models import Job
from apps.pipeline.probe import run_ffprobe
from apps.pipeline.scoring import run_segment_scoring, scoring_params_from_job


def _video_file_path(video: Video) -> Path:
    storage_root = Path(settings.NAKAVID_STORAGE_ROOT)
    relative_path = video.source_path.removeprefix("/nakavid/").lstrip("/")
    return storage_root / relative_path


def handle_probe(job: Job) -> None:
    video = job.video
    probe_result = run_ffprobe(_video_file_path(video))

    with transaction.atomic():
        video.duration_seconds = probe_result.duration_seconds
        video.orientation = probe_result.orientation
        video.video_codec = probe_result.video_codec
        video.width = probe_result.width
        video.height = probe_result.height
        video.save(
            update_fields=[
                "duration_seconds",
                "orientation",
                "video_codec",
                "width",
                "height",
                "updated_at",
            ]
        )

        if video.video_type == Video.VideoType.TYPE_B:
            clip = video.clips.order_by("id").first()
            if clip is not None:
                clip.end_seconds = Decimal(probe_result.duration_seconds)
                clip.save(update_fields=["end_seconds", "updated_at"])
        elif video.video_type == Video.VideoType.TYPE_A:
            enqueue_score_job(video=video)


def handle_ingest(job: Job) -> None:
    """Skeleton handler — real ingest pipeline stages land in later issues."""


def handle_clip_extraction(job: Job) -> None:
    """Skeleton handler — real clip extraction lands in later issues."""


def handle_score(job: Job) -> None:
    video = job.video
    if video.video_type != Video.VideoType.TYPE_A:
        return

    params = scoring_params_from_job(job)
    result = run_segment_scoring(
        video_path=_video_file_path(video),
        params=params,
        duration_seconds=video.duration_seconds,
    )

    with transaction.atomic():
        clip = video.clips.order_by("id").first()
        if clip is None:
            clip = Clip.objects.create(
                video=video,
                storage_path=video.source_path,
                start_seconds=Decimal("0.000"),
                end_seconds=Decimal(video.duration_seconds),
                created_by=video.created_by,
            )

        clip.end_seconds = Decimal(video.duration_seconds)
        clip.energy_curve = result.energy_curve
        clip.highlight_score = result.highlight_score
        clip.save(
            update_fields=[
                "end_seconds",
                "energy_curve",
                "highlight_score",
                "updated_at",
            ]
        )


JOB_HANDLERS = {
    Job.JobType.PROBE: handle_probe,
    Job.JobType.INGEST: handle_ingest,
    Job.JobType.CLIP_EXTRACTION: handle_clip_extraction,
    Job.JobType.SCORE: handle_score,
}


def dispatch_job(job: Job) -> None:
    handler = JOB_HANDLERS[job.job_type]
    handler(job)
