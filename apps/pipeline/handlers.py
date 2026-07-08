from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.db import transaction

from apps.library.models import Clip, Video
from apps.library.storage_paths import build_highlight_relative_paths, to_absolute_storage_path
from apps.pipeline.enqueue import enqueue_clip_extraction_job, enqueue_score_job
from apps.pipeline.extraction import (
    run_ffmpeg_thumbnail,
    run_ffmpeg_trim,
    select_clip_segments,
)
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
    video = job.video
    if video.video_type != Video.VideoType.TYPE_A:
        return

    params = scoring_params_from_job(job)
    scoring_clip = video.clips.filter(storage_path=video.source_path).order_by("-id").first()
    if scoring_clip is None or not scoring_clip.energy_curve:
        return

    source_stem = Path(video.source_path).stem
    duration_seconds = float(video.duration_seconds)
    selections = select_clip_segments(
        energy_curve=scoring_clip.energy_curve,
        params=params,
        duration_seconds=duration_seconds,
    )

    source_file_path = _video_file_path(video)
    storage_root = Path(settings.NAKAVID_STORAGE_ROOT)
    video_tag_ids = list(video.tags.values_list("id", flat=True))

    with transaction.atomic():
        video.clips.exclude(pk=scoring_clip.pk).delete()
        for clip_index, selection in enumerate(selections, start=1):
            relative_video_path, relative_thumbnail_path = build_highlight_relative_paths(
                recorded_at=video.recorded_at,
                class_name=video.class_name,
                theme=video.theme,
                source_stem=source_stem,
                clip_index=clip_index,
            )
            absolute_video_path = to_absolute_storage_path(storage_root, relative_video_path)
            absolute_thumbnail_path = to_absolute_storage_path(
                storage_root, relative_thumbnail_path
            )
            clip_file_path = storage_root / relative_video_path
            thumbnail_file_path = storage_root / relative_thumbnail_path

            run_ffmpeg_trim(
                source_path=source_file_path,
                target_path=clip_file_path,
                start_seconds=selection.start_seconds,
                end_seconds=selection.end_seconds,
            )
            run_ffmpeg_thumbnail(
                source_path=clip_file_path,
                target_path=thumbnail_file_path,
                at_seconds=max(0.0, (selection.end_seconds - selection.start_seconds) / 2.0),
            )

            clip = Clip.objects.create(
                video=video,
                storage_path=absolute_video_path,
                thumbnail_path=absolute_thumbnail_path,
                start_seconds=Decimal(f"{selection.start_seconds:.3f}"),
                end_seconds=Decimal(f"{selection.end_seconds:.3f}"),
                highlight_score=int(round(selection.score)),
                energy_curve=selection.energy_curve,
                created_by=video.created_by,
            )
            if video_tag_ids:
                clip.tags.set(video_tag_ids)

        scoring_clip.delete()


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
        enqueue_clip_extraction_job(video=video, scoring_params_id=params.pk)


JOB_HANDLERS = {
    Job.JobType.PROBE: handle_probe,
    Job.JobType.INGEST: handle_ingest,
    Job.JobType.CLIP_EXTRACTION: handle_clip_extraction,
    Job.JobType.SCORE: handle_score,
}


def dispatch_job(job: Job) -> None:
    handler = JOB_HANDLERS[job.job_type]
    handler(job)
