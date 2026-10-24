"""Web-triggered Immich import: the album picker, enqueueing, the job handler,
and the nav gate."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.library.immich import (
    ImmichAlbum,
    ImmichAsset,
    ImmichClient,
    ImmichError,
    immich_is_configured,
)
from apps.library.models import Video
from apps.pipeline.enqueue import enqueue_ingest_job
from apps.pipeline.handlers import handle_ingest
from apps.pipeline.models import Job

User = get_user_model()

ALBUM_ID = "993850f8-1f3d-41e6-989a-530600b97250"
OTHER_ALBUM_ID = "028a657e-f4f8-40ae-b129-87dbfc8b4c59"
ALBUMS = [
    ImmichAlbum(id=OTHER_ALBUM_ID, name="Media for Skippy", asset_count=3, shared=False),
    ImmichAlbum(id=ALBUM_ID, name="Skippy 2026", asset_count=3, shared=True),
]
ALBUM_ASSETS = [
    ImmichAsset(
        id="asset-1",
        original_file_name="already_here.mp4",
        created_at="2026-05-04T10:30:00Z",
        is_video=True,
    ),
    ImmichAsset(
        id="asset-2",
        original_file_name="brand_new.mov",
        created_at="2026-06-17T14:05:00Z",
        is_video=True,
    ),
    ImmichAsset(
        id="photo-1", original_file_name="snap.jpg", created_at="2026-06-17", is_video=False
    ),
]


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
    assert job.immich_album_id == ""


@pytest.mark.django_db
def test_handle_ingest_attributes_the_scan_to_the_single_superuser():
    """No --user equivalent on a web-triggered scan — falls back like the CLI does."""
    founder = User.objects.create_superuser(username="founder", password="secret123!")
    job = Job.objects.create(job_type=Job.JobType.INGEST)

    with patch("apps.pipeline.handlers.run_immich_ingest") as run:
        handle_ingest(job)

    run.assert_called_once_with(user=founder, album_id=None)


@pytest.mark.django_db
def test_handle_ingest_pulls_from_the_jobs_album():
    founder = User.objects.create_superuser(username="founder", password="secret123!")
    job = Job.objects.create(job_type=Job.JobType.INGEST, immich_album_id=ALBUM_ID)

    with patch("apps.pipeline.handlers.run_immich_ingest") as run:
        handle_ingest(job)

    run.assert_called_once_with(user=founder, album_id=ALBUM_ID)


def test_run_immich_ingest_defaults_match_the_cli_defaults():
    """handle_ingest leans entirely on these — nothing else pins tag/type.

    All three are None: resolved from IngestSettings (editable at
    /admin/pipeline/ingestsettings/) rather than pinned in code, so the web
    button and a bare CLI run both follow whatever's configured there.
    """
    import inspect

    from apps.library.immich_ingest import run_immich_ingest

    defaults = {
        name: param.default
        for name, param in inspect.signature(run_immich_ingest).parameters.items()
    }
    assert defaults["tag"] is None
    assert defaults["imported_tag"] is None
    assert defaults["video_type"] is None
    assert "orientation" not in defaults


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
def test_immich_scan_queues_an_album_import(authenticated_client):
    response = authenticated_client.post(reverse("immich-scan"), {"album_id": ALBUM_ID})

    assert response.status_code == 302
    assert response["Location"] == reverse("queue-status")
    job = Job.objects.get(job_type=Job.JobType.INGEST)
    assert job.immich_album_id == ALBUM_ID


@pytest.mark.django_db
def test_immich_scan_rejects_a_malformed_album_id(authenticated_client):
    response = authenticated_client.post(reverse("immich-scan"), {"album_id": "../etc"})

    assert response.status_code == 400
    assert not Job.objects.exists()


@pytest.fixture
def immich_albums(settings):
    settings.NAKAVID_IMMICH_URL = "http://127.0.0.1:2283"
    settings.NAKAVID_IMMICH_API_KEY = "a-key"
    with (
        patch.object(ImmichClient, "albums", return_value=ALBUMS),
        patch.object(ImmichClient, "album_assets", return_value=ALBUM_ASSETS) as album_assets,
    ):
        yield album_assets


@pytest.mark.django_db
def test_immich_import_requires_login(client):
    response = client.get(reverse("immich-import"))

    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")


@pytest.mark.django_db
def test_immich_import_lists_albums_including_shared_ones(authenticated_client, immich_albums):
    response = authenticated_client.get(reverse("immich-import"))

    content = response.content.decode()
    assert "Skippy 2026 (3 items) · shared" in content
    assert "Media for Skippy" in content
    # Nothing chosen and nothing pulled before: no album's contents fetched.
    immich_albums.assert_not_called()


@pytest.mark.django_db
def test_immich_import_previews_only_videos_new_to_nakavid(authenticated_client, immich_albums):
    founder = User.objects.get(username="coach")
    Video.objects.create(
        title="already_here",
        source_path="/nakavid/originals/2026/05/04/already_here.mp4",
        video_type=Video.VideoType.TYPE_B,
        recorded_at=timezone.now(),
        duration_seconds=10,
        immich_asset_id="asset-1",
        created_by=founder,
    )

    response = authenticated_client.get(reverse("immich-import"), {"album_id": ALBUM_ID})

    content = response.content.decode()
    immich_albums.assert_called_once_with(ALBUM_ID)
    assert "2 videos in this album, 1 new to NakaVid" in content
    assert "brand_new.mov" in content
    assert "already_here.mp4" not in content
    assert "snap.jpg" not in content
    assert "Import 1 new video<" in content
    assert f'name="album_id" value="{ALBUM_ID}"' in content


@pytest.mark.django_db
def test_immich_import_preselects_the_last_album_pulled_from(authenticated_client, immich_albums):
    Job.objects.create(job_type=Job.JobType.INGEST, immich_album_id=OTHER_ALBUM_ID)
    Job.objects.create(job_type=Job.JobType.INGEST, immich_album_id=ALBUM_ID)
    Job.objects.create(job_type=Job.JobType.INGEST)  # a later tag scan doesn't count

    response = authenticated_client.get(reverse("immich-import"))

    immich_albums.assert_called_once_with(ALBUM_ID)
    assert f'<option value="{ALBUM_ID}" selected>' in response.content.decode()


@pytest.mark.django_db
def test_immich_import_shows_an_immich_error_instead_of_failing(authenticated_client, settings):
    settings.NAKAVID_IMMICH_URL = "http://127.0.0.1:2283"
    settings.NAKAVID_IMMICH_API_KEY = "a-key"
    with patch.object(ImmichClient, "albums", side_effect=ImmichError("Immich is down")):
        response = authenticated_client.get(reverse("immich-import"))

    assert response.status_code == 200
    assert "Immich is down" in response.content.decode()


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

    assert b"From Immich" not in response.content


@pytest.mark.django_db
def test_nav_shows_scan_immich_when_configured(authenticated_client, settings):
    settings.NAKAVID_IMMICH_URL = "https://photos.example"
    settings.NAKAVID_IMMICH_API_KEY = "a-key"

    response = authenticated_client.get(reverse("clips-browser"))

    assert b"From Immich" in response.content
    assert reverse("immich-import").encode() in response.content
