from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.pipeline.models import Job

# A job claim is a lease, not a lock. Nothing releases it if the worker dies,
# so without this a restart strands work in "processing" forever — the queue
# only ever claims pending rows, and the re-queue button only offers errors.
DEFAULT_CLAIM_TIMEOUT_MINUTES = 60


def claim_timeout() -> timedelta:
    minutes = getattr(settings, "NAKAVID_JOB_CLAIM_TIMEOUT_MINUTES", DEFAULT_CLAIM_TIMEOUT_MINUTES)
    return timedelta(minutes=max(int(minutes), 1))


def reclaim_stale_jobs() -> int:
    """Return jobs whose worker went away to the queue.

    Generous by design: scoring a long recording legitimately runs for many
    minutes, and re-running a job that is still in flight would double the
    work. The timeout only needs to be shorter than "forever".
    """
    cutoff = timezone.now() - claim_timeout()
    with transaction.atomic():
        stale = list(
            Job.objects.filter(status=Job.Status.PROCESSING, claimed_at__lt=cutoff)
            .select_for_update(skip_locked=True)
            .order_by("id")
        )
        for job in stale:
            job.status = Job.Status.PENDING
            job.claimed_at = None
            job.finished_at = None
            job.stderr = (
                "Reclaimed: this job held a claim with no worker behind it, so it "
                "was returned to the queue."
            )
            job.save(
                update_fields=[
                    "status",
                    "claimed_at",
                    "finished_at",
                    "stderr",
                    "updated_at",
                ]
            )
    return len(stale)


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
