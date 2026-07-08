from __future__ import annotations

from apps.library.models import Video
from apps.pipeline.models import Job

STUB_DURATION_SECONDS = 1


def enqueue_probe_job(*, video: Video) -> Job:
    return Job.objects.create(video=video, job_type=Job.JobType.PROBE)
