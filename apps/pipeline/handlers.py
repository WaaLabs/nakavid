from __future__ import annotations

from collections.abc import Callable

from apps.pipeline.models import Job


def handle_ingest(job: Job) -> None:
    """Skeleton handler — real ingest pipeline stages land in later issues."""


def handle_clip_extraction(job: Job) -> None:
    """Skeleton handler — real clip extraction lands in later issues."""


def handle_score(job: Job) -> None:
    """Skeleton handler — real scoring lands in later issues."""


JOB_HANDLERS: dict[str, Callable[[Job], None]] = {
    Job.JobType.INGEST: handle_ingest,
    Job.JobType.CLIP_EXTRACTION: handle_clip_extraction,
    Job.JobType.SCORE: handle_score,
}


def dispatch_job(job: Job) -> None:
    handler = JOB_HANDLERS[job.job_type]
    handler(job)
