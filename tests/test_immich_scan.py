"""Web-triggered Immich scan: enqueueing, the job handler, and the nav gate."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.library.immich import ImmichError, immich_is_configured
from apps.library.immich_ingest import DEFAULT_IMPORTED_TAG, DEFAULT_TAG
from apps.library.models import Video
from apps.pipeline.enqueue import enqueue_ingest_job
from apps.pipeline.handlers import handle_ingest
from apps.pipeline.models import Job

User = get_user_model()


@pytest.fixture
def authenticated_client(client, db):
    User.objects.create_user(username="coach", password="secret123!")
    assert client.login(username="coach", password="secret123!")
    return client


@pytest.mark.django_db
def test_enqueue_ingest_job_has_no_video():
    job = enqueue_ingest_job()

    assert job.video is None
    assert job.job_type == Job.JobType.INGEST
    assert job.status == Job.Status.PENDING


@pytest.mark.django_db
def test_handle_ingest_attributes_the_scan_to_the_single_superuser():
    """No --user equivalent on a web-triggered scan — falls back like the CLI does."""
    founder = User.objects.create_superuser(username="founder", password="secret123!")
    job = Job.objects.create(job_type=Job.JobType.INGEST)

    with patch("apps.pipeline.handlers.run_immich_ingest") as run:
        handle_ingest(job)

    run.assert_called_once_with(user=founder)


def test_run_immich_ingest_defaults_match_the_cli_defaults():
    """handle_ingest leans entirely on these — nothing else pins tag/type/orientation."""
    import inspect

    from apps.library.immich_ingest import run_immich_ingest

    defaults = {
        name: param.default
        for name, param in inspect.signature(run_immich_ingest).parameters.items()
    }
    assert defaults["tag"] == DEFAULT_TAG
    assert defaults["imported_tag"] == DEFAULT_IMPORTED_TAG
    assert defaults["video_type"] == Video.VideoType.TYPE_A
    assert defaults["orientation"] == Video.Orientation.LANDSCAPE


@pytest.mark.django_db
def test_handle_ingest_errors_without_exactly_one_superuser():
    """No --user equivalent on a web-triggered scan — must be unambiguous."""
    job = Job.objects.create(job_type=Job.JobType.INGEST)

    with pytest.raises(ImmichError, match="--user"):
        handle_ingest(job)


def test_immich_is_configured(settings):
    settings.NAKAVID_IMMICH_URL = ""
    settings.NAKAVID_IMMICH_API_KEY = ""
    assert immich_is_configured() is False

    settings.NAKAVID_IMMICH_URL = "https://photos.example"
    settings.NAKAVID_IMMICH_API_KEY = ""
    assert immich_is_configured() is False

    settings.NAKAVID_IMMICH_API_KEY = "a-key"
    assert immich_is_configured() is True


@pytest.mark.django_db
def test_immich_scan_requires_login(client):
    response = client.post(reverse("immich-scan"))

    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")


@pytest.mark.django_db
def test_immich_scan_is_post_only(authenticated_client):
    response = authenticated_client.get(reverse("immich-scan"))

    assert response.status_code == 405


@pytest.mark.django_db
def test_immich_scan_queues_a_job_and_redirects(authenticated_client):
    response = authenticated_client.post(reverse("immich-scan"))

    assert response.status_code == 302
    assert response["Location"] == reverse("queue-status")
    job = Job.objects.get(job_type=Job.JobType.INGEST)
    assert job.video is None
    assert job.status == Job.Status.PENDING


@pytest.mark.django_db
def test_nav_hides_scan_immich_when_not_configured(authenticated_client, settings):
    settings.NAKAVID_IMMICH_URL = ""
    settings.NAKAVID_IMMICH_API_KEY = ""

    response = authenticated_client.get(reverse("clips-browser"))

    assert b"Scan Immich" not in response.content


@pytest.mark.django_db
def test_nav_shows_scan_immich_when_configured(authenticated_client, settings):
    settings.NAKAVID_IMMICH_URL = "https://photos.example"
    settings.NAKAVID_IMMICH_API_KEY = "a-key"

    response = authenticated_client.get(reverse("clips-browser"))

    assert b"Scan Immich" in response.content
    assert reverse("immich-scan").encode() in response.content
