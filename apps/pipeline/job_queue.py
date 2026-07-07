from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.pipeline.models import Job


def claim_next_job() -> Job | None:
    """Atomically claim the oldest pending job using SKIP LOCKED."""
    with transaction.atomic():
        job = (
            Job.objects.filter(status=Job.Status.PENDING)
            .order_by("created_at", "id")
            .select_for_update(skip_locked=True)
            .first()
        )
        if job is None:
            return None

        job.status = Job.Status.PROCESSING
        job.claimed_at = timezone.now()
        job.save(update_fields=["status", "claimed_at", "updated_at"])
        return job


def mark_job_done(job: Job) -> None:
    job.status = Job.Status.DONE
    job.finished_at = timezone.now()
    job.stderr = ""
    job.save(update_fields=["status", "finished_at", "stderr", "updated_at"])


def mark_job_error(job: Job, stderr: str) -> None:
    job.status = Job.Status.ERROR
    job.finished_at = timezone.now()
    job.stderr = stderr
    job.save(update_fields=["status", "finished_at", "stderr", "updated_at"])
