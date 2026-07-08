from __future__ import annotations

import traceback

from apps.pipeline.handlers import dispatch_job
from apps.pipeline.job_queue import mark_job_done, mark_job_error
from apps.pipeline.models import Job
from apps.pipeline.probe import ProbeError
from apps.pipeline.scoring import ScoringError


def process_job(job: Job) -> None:
    try:
        dispatch_job(job)
    except ProbeError as exc:
        mark_job_error(job, stderr=str(exc))
        return
    except ScoringError as exc:
        mark_job_error(job, stderr=str(exc))
        return
    except Exception as exc:
        mark_job_error(job, stderr=_format_stderr(exc))
        return

    mark_job_done(job)


def _format_stderr(exc: BaseException) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
