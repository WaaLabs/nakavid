import threading
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connections
from django.utils import timezone

from apps.library.models import Video
from apps.pipeline.handlers import dispatch_job
from apps.pipeline.job_queue import claim_next_job, mark_job_done, mark_job_error
from apps.pipeline.models import Job
from apps.pipeline.scoring import SegmentScoringResult
from apps.pipeline.worker import process_job

User = get_user_model()


@pytest.fixture
def video(db):
    user = User.objects.create_user(username="worker-test", password="secret123!")
    return Video.objects.create(
        title="Worker Test Video",
        source_path="/nakavid/originals/2026/07/20260707_worker_test/source.mp4",
        video_type=Video.VideoType.TYPE_A,
        orientation=Video.Orientation.LANDSCAPE,
        class_name="A",
        theme="Worker",
        recorded_at=timezone.now(),
        duration_seconds=120,
        is_private=True,
        created_by=user,
    )


@pytest.fixture
def pending_job(video):
    return Job.objects.create(video=video, job_type=Job.JobType.CLIP_EXTRACTION)


@pytest.mark.django_db
def test_claim_next_job_transitions_pending_to_processing(pending_job):
    claimed = claim_next_job()

    assert claimed is not None
    assert claimed.pk == pending_job.pk
    claimed.refresh_from_db()
    assert claimed.status == Job.Status.PROCESSING
    assert claimed.claimed_at is not None


@pytest.mark.django_db
def test_claim_next_job_returns_none_when_queue_empty():
    assert claim_next_job() is None


@pytest.mark.django_db
def test_claim_next_job_skips_non_pending_jobs(pending_job):
    pending_job.status = Job.Status.PROCESSING
    pending_job.save(update_fields=["status"])

    assert claim_next_job() is None


@pytest.mark.django_db
def test_mark_job_done_and_error_persist_state(pending_job):
    pending_job.status = Job.Status.PROCESSING
    pending_job.claimed_at = timezone.now()
    pending_job.save(update_fields=["status", "claimed_at"])

    mark_job_done(pending_job)
    pending_job.refresh_from_db()
    assert pending_job.status == Job.Status.DONE
    assert pending_job.finished_at is not None
    assert pending_job.stderr == ""

    pending_job.status = Job.Status.PROCESSING
    pending_job.finished_at = None
    pending_job.save(update_fields=["status", "finished_at"])

    mark_job_error(pending_job, stderr="ffmpeg failed: exit 1")
    pending_job.refresh_from_db()
    assert pending_job.status == Job.Status.ERROR
    assert pending_job.finished_at is not None
    assert pending_job.stderr == "ffmpeg failed: exit 1"


@pytest.mark.django_db
def test_process_job_marks_done_for_skeleton_handler(pending_job):
    pending_job.status = Job.Status.PROCESSING
    pending_job.claimed_at = timezone.now()
    pending_job.save(update_fields=["status", "claimed_at"])

    process_job(pending_job)

    pending_job.refresh_from_db()
    assert pending_job.status == Job.Status.DONE
    assert pending_job.finished_at is not None
    assert pending_job.stderr == ""


@pytest.mark.django_db
def test_process_job_captures_handler_errors_on_stderr(pending_job):
    pending_job.status = Job.Status.PROCESSING
    pending_job.claimed_at = timezone.now()
    pending_job.save(update_fields=["status", "claimed_at"])

    with patch("apps.pipeline.worker.dispatch_job", side_effect=RuntimeError("probe failed")):
        process_job(pending_job)

    pending_job.refresh_from_db()
    assert pending_job.status == Job.Status.ERROR
    assert pending_job.finished_at is not None
    assert "probe failed" in pending_job.stderr
    assert "RuntimeError" in pending_job.stderr


@pytest.mark.django_db
def test_dispatch_job_routes_all_job_types(video):
    probe_result = __import__("apps.pipeline.probe", fromlist=["ProbeResult"]).ProbeResult(
        duration_seconds=120,
        orientation=Video.Orientation.LANDSCAPE,
        video_codec="h264",
        width=1920,
        height=1080,
    )
    with patch("apps.pipeline.handlers.run_ffprobe", return_value=probe_result):
        scoring_result = SegmentScoringResult(energy_curve=[], highlight_score=0)
        with patch("apps.pipeline.handlers.run_segment_scoring", return_value=scoring_result):
            for job_type in Job.JobType.values:
                job = Job.objects.create(
                    video=video, job_type=job_type, status=Job.Status.PROCESSING
                )
                dispatch_job(job)


@pytest.mark.django_db(transaction=True)
def test_concurrent_workers_do_not_double_claim(video):
    job = Job.objects.create(video=video, job_type=Job.JobType.SCORE)
    claimed_ids: list[int] = []
    barrier = threading.Barrier(2)

    def worker_claim() -> None:
        connections.close_all()
        barrier.wait()
        claimed = claim_next_job()
        if claimed is not None:
            claimed_ids.append(claimed.pk)

    threads = [threading.Thread(target=worker_claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(claimed_ids) == 1
    assert claimed_ids[0] == job.pk
    job.refresh_from_db()
    assert job.status == Job.Status.PROCESSING


@pytest.mark.django_db
def test_run_worker_once_processes_single_job(pending_job):
    call_command("run_worker", once=True)

    pending_job.refresh_from_db()
    assert pending_job.status == Job.Status.DONE
    assert pending_job.finished_at is not None


@pytest.mark.django_db
def test_run_worker_once_exits_when_queue_empty(capsys):
    call_command("run_worker", once=True)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
