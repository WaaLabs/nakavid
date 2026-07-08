from __future__ import annotations

from datetime import datetime

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.library.models import Video
from apps.library.storage_paths import to_absolute_storage_path
from apps.pipeline.models import Job

User = get_user_model()


@pytest.fixture
def storage_root(tmp_path, settings):
    settings.NAKAVID_STORAGE_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def coach_user(db):
    return User.objects.create_user(username="coach", password="secret123!")


@pytest.fixture
def authenticated_client(client, coach_user):
    assert client.login(username="coach", password="secret123!")
    return client, coach_user


@pytest.fixture
def sample_video(db, coach_user, storage_root):
    recorded_at = timezone.make_aware(datetime(2026, 7, 1, 9, 0))
    return Video.objects.create(
        title="lesson",
        source_path=to_absolute_storage_path(storage_root, "originals/2026/07/20260701_a_animals/lesson.mp4"),
        video_type=Video.VideoType.TYPE_A,
        orientation=Video.Orientation.LANDSCAPE,
        class_name="A",
        theme="Animals",
        recorded_at=recorded_at,
        duration_seconds=3600,
        created_by=coach_user,
    )


@pytest.mark.django_db
def test_queue_status_requires_login(client):
    response = client.get(reverse("queue-status"))

    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")


@pytest.mark.django_db
def test_queue_status_shows_totals_and_recent_jobs(authenticated_client, sample_video):
    client, _user = authenticated_client

    Job.objects.create(video=sample_video, job_type=Job.JobType.PROBE, status=Job.Status.PENDING)
    Job.objects.create(video=sample_video, job_type=Job.JobType.SCORE, status=Job.Status.PROCESSING)
    Job.objects.create(video=sample_video, job_type=Job.JobType.CLIP_EXTRACTION, status=Job.Status.DONE)
    Job.objects.create(video=sample_video, job_type=Job.JobType.INGEST, status=Job.Status.ERROR)

    response = client.get(reverse("queue-status"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Queue Status" in content
    assert "Pending" in content
    assert "Processing" in content
    assert "Done" in content
    assert "Error" in content
    assert "Probe" in content
    assert "Score" in content
    assert "Clip Extraction" in content
    assert "Ingest" in content
    assert "lesson" in content
