"""Pulling footage from a self-hosted Immich album."""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command

from apps.library.immich import ImmichAsset, ImmichClient, ImmichError, configured_base_url
from apps.library.models import Video
from apps.pipeline.models import Job

User = get_user_model()


@pytest.fixture
def storage_root(tmp_path, settings):
    settings.NAKAVID_STORAGE_ROOT = tmp_path
    # Loopback, so the client's private-address check runs for real.
    settings.NAKAVID_IMMICH_URL = "http://127.0.0.1:2283"
    settings.NAKAVID_IMMICH_API_KEY = "test-key"
    return tmp_path


@pytest.fixture
def superuser(db):
    return User.objects.create_superuser(username="founder", password="secret123!")


ASSETS = [
    ImmichAsset(
        id="asset-1",
        original_file_name="lesson_one.mp4",
        created_at="2026-05-04T10:30:00.000Z",
        is_video=True,
    ),
    ImmichAsset(
        id="asset-2",
        original_file_name="lesson_two.mov",
        created_at="2026-06-17T14:05:00.000Z",
        is_video=True,
    ),
    ImmichAsset(
        id="photo-1",
        original_file_name="snap.jpg",
        created_at="2026-06-17T14:06:00Z",
        is_video=False,
    ),
]


def _run(**kwargs):
    out = StringIO()
    downloads: list[tuple[str, Path]] = []

    def fake_download(self, asset_id, target_path):
        downloads.append((asset_id, target_path))
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(b"video bytes")

    with (
        patch.object(ImmichClient, "album_named", return_value={"id": "album-1"}),
        patch.object(ImmichClient, "album_assets", return_value=ASSETS),
        patch.object(ImmichClient, "download_asset", fake_download),
    ):
        call_command(
            "ingest_immich",
            album="nakavid",
            class_name="Quokka",
            theme="Lesson",
            stdout=out,
            **kwargs,
        )
    return out.getvalue(), downloads


@pytest.mark.django_db
def test_pulls_only_the_videos(storage_root, superuser):
    output, downloads = _run()

    assert Video.objects.count() == 2
    assert {asset_id for asset_id, _ in downloads} == {"asset-1", "asset-2"}
    # The photo in the album is left alone.
    assert "snap.jpg" not in output
    assert "3 asset(s), 2 video(s)" in output


@pytest.mark.django_db
def test_files_land_under_their_own_recording_dates(storage_root, superuser):
    _run()

    paths = sorted(video.source_path for video in Video.objects.all())
    assert "originals/2026/05/20260504_quokka_lesson/lesson_one.mp4" in paths[0]
    assert "originals/2026/06/20260617_quokka_lesson/lesson_two.mov" in paths[1]


@pytest.mark.django_db
def test_each_video_is_queued_for_probing(storage_root, superuser):
    _run()

    for video in Video.objects.all():
        assert Job.objects.filter(video=video, job_type=Job.JobType.PROBE).exists()


@pytest.mark.django_db
def test_is_idempotent_by_immich_asset_id(storage_root, superuser):
    """Filenames repeat across albums; the asset id is the stable identity."""
    _run()
    output, downloads = _run()

    assert Video.objects.count() == 2
    assert downloads == []
    assert "skipped 2 already in the library" in output


@pytest.mark.django_db
def test_dry_run_downloads_nothing(storage_root, superuser):
    output, downloads = _run(dry_run=True)

    assert Video.objects.count() == 0
    assert downloads == []
    assert "would pull 2 video(s)" in output


@pytest.mark.django_db
def test_limit_stops_early(storage_root, superuser):
    _run(limit=1)

    assert Video.objects.count() == 1


@pytest.mark.django_db
def test_short_recordings_get_their_clip_row(storage_root, superuser):
    _run(type="short")

    for video in Video.objects.all():
        assert video.video_type == Video.VideoType.TYPE_B
        assert video.clips.count() == 1


@pytest.mark.django_db
def test_a_failed_download_does_not_create_a_row(storage_root, superuser):
    def explode(self, asset_id, target_path):
        raise ImmichError("connection reset")

    with (
        patch.object(ImmichClient, "album_named", return_value={"id": "album-1"}),
        patch.object(ImmichClient, "album_assets", return_value=ASSETS[:1]),
        patch.object(ImmichClient, "download_asset", explode),
    ):
        call_command(
            "ingest_immich",
            album="nakavid",
            class_name="A",
            theme="B",
            stdout=StringIO(),
            stderr=StringIO(),
        )

    assert Video.objects.count() == 0


@pytest.mark.django_db
def test_a_missing_album_is_reported_clearly(storage_root, superuser):
    with patch.object(ImmichClient, "albums", return_value=[{"id": "x", "albumName": "Holidays"}]):
        with pytest.raises(CommandError, match="No Immich album named 'nakavid'"):
            call_command("ingest_immich", album="nakavid", class_name="A", theme="B")


def test_a_public_immich_host_is_refused(settings):
    """Footage must not leave the LAN, so a routable host is not usable."""
    settings.NAKAVID_IMMICH_URL = "https://photos.example.com"
    settings.NAKAVID_IMMICH_API_KEY = "k"

    with patch(
        "apps.library.immich.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 0))]
    ):
        with pytest.raises(ImmichError, match="does not resolve to a private address"):
            configured_base_url()


def test_a_private_immich_host_is_allowed(settings):
    settings.NAKAVID_IMMICH_URL = "https://photos.crty.io"
    settings.NAKAVID_IMMICH_API_KEY = "k"

    with patch(
        "apps.library.immich.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.89.24.1", 0))]
    ):
        assert configured_base_url() == "https://photos.crty.io"


def test_a_missing_api_key_says_where_to_get_one(settings):
    settings.NAKAVID_IMMICH_URL = "https://photos.crty.io"
    settings.NAKAVID_IMMICH_API_KEY = ""

    from apps.library.immich import configured_api_key

    with pytest.raises(ImmichError, match="Account Settings"):
        configured_api_key()


def _client_with_responses(responses: dict[str, object]) -> ImmichClient:
    client = ImmichClient(base_url="http://127.0.0.1:2283", api_key="k")
    client._get_json = lambda path: responses[path]  # type: ignore[method-assign]
    return client


def test_albums_include_ones_shared_from_another_account():
    """Footage filmed on one login is often collected under another."""
    client = _client_with_responses(
        {
            "/api/albums": [{"id": "own-1", "albumName": "My Stuff"}],
            "/api/albums?shared=true": [{"id": "shared-1", "albumName": "nakavid"}],
        }
    )

    names = {album["albumName"] for album in client.albums()}

    assert names == {"My Stuff", "nakavid"}
    assert client.album_named("nakavid")["id"] == "shared-1"


def test_an_album_listed_twice_is_not_duplicated():
    """A shared album you also own must not look like two albums."""
    album = {"id": "both-1", "albumName": "nakavid"}
    client = _client_with_responses({"/api/albums": [album], "/api/albums?shared=true": [album]})

    assert len(client.albums()) == 1
    assert client.album_named("nakavid")["id"] == "both-1"
