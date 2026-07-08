from __future__ import annotations

from apps.library.models import Video
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
