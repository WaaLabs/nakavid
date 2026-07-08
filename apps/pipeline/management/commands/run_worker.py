from __future__ import annotations

import time

from django.core.management.base import BaseCommand

from apps.pipeline.job_queue import claim_next_job
from apps.pipeline.worker import process_job


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

        while True:
            job = claim_next_job()
            if job is None:
                if run_once:
                    return
                time.sleep(poll_interval)
                continue

            self.stdout.write(f"Processing job {job.pk} ({job.job_type})")
            process_job(job)
            self.stdout.write(f"Finished job {job.pk} ({job.status})")

            if run_once:
                return
