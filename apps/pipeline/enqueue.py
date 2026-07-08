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
