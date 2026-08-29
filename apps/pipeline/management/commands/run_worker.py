from __future__ import annotations

import time

from django.core.management.base import BaseCommand

from apps.pipeline.job_queue import claim_next_job, reclaim_stale_jobs
from apps.pipeline.worker import process_job

RECLAIM_SWEEP_SECONDS = 60.0


class Command(BaseCommand):
    help = "Claim and process pending pipeline jobs from the Postgres queue."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--once",
            action="store_true",
            help="Process at most one job, then exit.",
        )
        parser.add_argument(
            "--poll-interval",
            type=float,
            default=1.0,
            help="Seconds to wait when no pending jobs are available.",
        )

    def handle(self, *args, **options) -> None:
        poll_interval: float = options["poll_interval"]
        run_once: bool = options["once"]

        # A worker that died mid-job left its claim behind; take those back
        # before looking for new work.
        reclaimed = reclaim_stale_jobs()
        if reclaimed:
            self.stdout.write(f"Reclaimed {reclaimed} stale job(s)")

        # Polling is every second; sweeping for stale claims that often would
        # be a query per second for something that changes on the scale of the
        # claim timeout.
        last_sweep = time.monotonic()

        while True:
            job = claim_next_job()
            if job is None:
                if run_once:
                    return
                time.sleep(poll_interval)
                if time.monotonic() - last_sweep >= RECLAIM_SWEEP_SECONDS:
                    last_sweep = time.monotonic()
                    reclaimed = reclaim_stale_jobs()
                    if reclaimed:
                        self.stdout.write(f"Reclaimed {reclaimed} stale job(s)")
                continue

            self.stdout.write(f"Processing job {job.pk} ({job.job_type})")
            process_job(job)
            self.stdout.write(f"Finished job {job.pk} ({job.status})")

            if run_once:
                return
