from __future__ import annotations

from apps.library.models import Combine, Video
from apps.pipeline.models import Job
from apps.pipeline.scoring import get_active_scoring_params

STUB_DURATION_SECONDS = 1


def enqueue_probe_job(*, video: Video) -> Job:
    return Job.objects.create(video=video, job_type=Job.JobType.PROBE)


def enqueue_score_job(*, video: Video) -> Job:
    params = get_active_scoring_params()
    return Job.objects.create(
        video=video,
        job_type=Job.JobType.SCORE,
        scoring_params=params,
    )


def enqueue_clip_extraction_job(*, video: Video, scoring_params_id: int | None) -> Job:
    return Job.objects.create(
        video=video,
        job_type=Job.JobType.CLIP_EXTRACTION,
        scoring_params_id=scoring_params_id,
    )


def enqueue_combine_export_job(*, combine: Combine) -> Job:
    first_combine_clip = (
        combine.combine_clips.select_related("clip__video").order_by("position").first()
    )
    if first_combine_clip is None:
        raise ValueError("Combine has no clips")
    return Job.objects.create(
        video=first_combine_clip.clip.video,
        combine=combine,
        job_type=Job.JobType.COMBINE_EXPORT,
    )
