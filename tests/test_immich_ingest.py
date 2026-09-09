"""Pulling footage tagged for import from a self-hosted Immich instance."""

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


def _fake_download(self, asset_id, target_path):
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(b"video bytes")


def _run(**kwargs):
    out = StringIO()
    downloads: list[tuple[str, Path]] = []
    retags: dict[str, list] = {"tagged": [], "untagged": []}

    def fake_download(self, asset_id, target_path):
        downloads.append((asset_id, target_path))
        _fake_download(self, asset_id, target_path)

    def fake_tag_assets(self, tag_id, asset_ids):
        retags["tagged"].append((tag_id, list(asset_ids)))

    def fake_untag_assets(self, tag_id, asset_ids):
        retags["untagged"].append((tag_id, list(asset_ids)))

    with (
        patch.object(ImmichClient, "tag_named", return_value={"id": "tag-1"}),
        patch.object(ImmichClient, "tagged_assets", return_value=ASSETS),
        patch.object(ImmichClient, "download_asset", fake_download),
        patch.object(ImmichClient, "upsert_tag", return_value={"id": "imported-tag-1"}),
        patch.object(ImmichClient, "tag_assets", fake_tag_assets),
        patch.object(ImmichClient, "untag_assets", fake_untag_assets),
    ):
        call_command(
            "ingest_immich",
            class_name="Quokka",
            theme="Lesson",
            stdout=out,
            **kwargs,
        )
    return out.getvalue(), downloads, retags


@pytest.mark.django_db
def test_pulls_only_the_videos(storage_root, superuser):
    output, downloads, _ = _run()

    assert Video.objects.count() == 2
    assert {asset_id for asset_id, _ in downloads} == {"asset-1", "asset-2"}
    # The photo carrying the tag is left alone.
    assert "snap.jpg" not in output
    assert "3 asset(s), 2 video(s)" in output


@pytest.mark.django_db
def test_imported_videos_are_retagged_in_immich(storage_root, superuser):
    output, _, retags = _run()

    assert retags["tagged"] == [("imported-tag-1", ["asset-1", "asset-2"])]
    assert retags["untagged"] == [("tag-1", ["asset-1", "asset-2"])]
    assert "retagged 2 video(s) as 'Nakavid/imported' in Immich" in output


@pytest.mark.django_db
def test_skipped_videos_are_not_retagged(storage_root, superuser):
    """Already-imported assets were retagged on a previous run; leave them."""
    _run()
    _, _, retags = _run()

    assert retags == {"tagged": [], "untagged": []}


@pytest.mark.django_db
def test_a_failed_retag_is_a_warning_not_a_failure(storage_root, superuser):
    """The videos are already safely in NakaVid; a retag hiccup must not undo that."""
    out = StringIO()
    err = StringIO()
    with (
        patch.object(ImmichClient, "tag_named", return_value={"id": "tag-1"}),
        patch.object(ImmichClient, "tagged_assets", return_value=ASSETS),
        patch.object(ImmichClient, "download_asset", _fake_download),
        patch.object(ImmichClient, "upsert_tag", side_effect=ImmichError("Immich is down")),
    ):
        call_command("ingest_immich", class_name="Quokka", theme="Lesson", stdout=out, stderr=err)

    assert Video.objects.count() == 2
    assert "warning: imported 2 video(s) but could not retag them" in err.getvalue()


@pytest.mark.django_db
def test_the_imported_tag_name_can_be_overridden(storage_root, superuser):
    with (
        patch.object(ImmichClient, "tag_named", return_value={"id": "tag-1"}),
        patch.object(ImmichClient, "tagged_assets", return_value=ASSETS),
        patch.object(ImmichClient, "download_asset", _fake_download),
        patch.object(ImmichClient, "upsert_tag", return_value={"id": "custom-1"}) as upsert_tag,
    ):
        call_command(
            "ingest_immich",
            imported_tag="Nakavid/done",
            class_name="Quokka",
            theme="Lesson",
            stdout=StringIO(),
        )

    upsert_tag.assert_called_once_with("Nakavid/done")


@pytest.mark.django_db
def test_defaults_to_the_nakavid_add_tag(storage_root, superuser):
    with (
        patch.object(ImmichClient, "tag_named", return_value={"id": "tag-1"}) as tag_named,
        patch.object(ImmichClient, "tagged_assets", return_value=[]),
    ):
        call_command("ingest_immich", class_name="Quokka", theme="Lesson", stdout=StringIO())

    tag_named.assert_called_once_with("Nakavid/add")


@pytest.mark.django_db
def test_a_different_tag_can_be_selected(storage_root, superuser):
    with (
        patch.object(ImmichClient, "tag_named", return_value={"id": "tag-2"}) as tag_named,
        patch.object(ImmichClient, "tagged_assets", return_value=[]) as tagged_assets,
    ):
        call_command(
            "ingest_immich",
            tag="Nakavid/review",
            class_name="Quokka",
            theme="Lesson",
            stdout=StringIO(),
        )

    tag_named.assert_called_once_with("Nakavid/review")
    tagged_assets.assert_called_once_with("tag-2")


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
    """Filenames repeat across tags; the asset id is the stable identity."""
    _run()
    output, downloads, _ = _run()

    assert Video.objects.count() == 2
    assert downloads == []
    assert "skipped 2 already in the library" in output


@pytest.mark.django_db
def test_dry_run_downloads_nothing(storage_root, superuser):
    output, downloads, retags = _run(dry_run=True)

    assert Video.objects.count() == 0
    assert downloads == []
    assert "would pull 2 video(s)" in output
    # A dry run writes nothing, in Immich either.
    assert retags == {"tagged": [], "untagged": []}


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
        patch.object(ImmichClient, "tag_named", return_value={"id": "tag-1"}),
        patch.object(ImmichClient, "tagged_assets", return_value=ASSETS[:1]),
        patch.object(ImmichClient, "download_asset", explode),
    ):
        call_command(
            "ingest_immich",
            class_name="A",
            theme="B",
            stdout=StringIO(),
            stderr=StringIO(),
        )

    assert Video.objects.count() == 0


@pytest.mark.django_db
def test_a_missing_tag_is_reported_clearly(storage_root, superuser):
    with patch.object(ImmichClient, "tags", return_value=[{"id": "x", "value": "Holidays"}]):
        with pytest.raises(CommandError, match="No Immich tag named 'Nakavid/add'"):
            call_command("ingest_immich", class_name="A", theme="B")


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


def test_tag_named_matches_on_the_full_hierarchical_path():
    """Immich reports a nested tag's full path in 'value', not just the leaf."""
    client = _client_with_responses(
        {"/api/tags": [{"id": "t1", "value": "Nakavid/add"}, {"id": "t2", "value": "Holidays"}]}
    )

    assert client.tag_named("Nakavid/add")["id"] == "t1"


def test_a_tag_listed_twice_is_reported_as_ambiguous():
    client = _client_with_responses(
        {
            "/api/tags": [
                {"id": "t1", "value": "Nakavid/add"},
                {"id": "t2", "value": "nakavid/add"},
            ]
        }
    )

    with pytest.raises(ImmichError, match="More than one Immich tag"):
        client.tag_named("Nakavid/add")


def _client_with_send(responses: dict[tuple[str, str], object]) -> tuple[ImmichClient, list]:
    client = ImmichClient(base_url="http://127.0.0.1:2283", api_key="k")
    calls: list[tuple[str, str, dict]] = []

    def fake_send(method, path, body):
        calls.append((method, path, body))
        return responses[(method, path)]

    client._send_json = fake_send  # type: ignore[method-assign]
    return client, calls


def test_upsert_tag_returns_the_matching_tag():
    client, calls = _client_with_send(
        {("PUT", "/api/tags"): [{"id": "t1", "value": "Nakavid/imported"}]}
    )

    tag = client.upsert_tag("Nakavid/imported")

    assert tag["id"] == "t1"
    assert calls == [("PUT", "/api/tags", {"tags": ["Nakavid/imported"]})]


def test_upsert_tag_reports_a_response_missing_the_tag():
    client, _ = _client_with_send({("PUT", "/api/tags"): [{"id": "t1", "value": "Other"}]})

    with pytest.raises(ImmichError, match="did not return the upserted tag"):
        client.upsert_tag("Nakavid/imported")


def test_tag_assets_sends_the_asset_ids():
    client, calls = _client_with_send({("PUT", "/api/tags/t1/assets"): None})

    client.tag_assets("t1", ["a1", "a2"])

    assert calls == [("PUT", "/api/tags/t1/assets", {"ids": ["a1", "a2"]})]


def test_untag_assets_sends_the_asset_ids():
    client, calls = _client_with_send({("DELETE", "/api/tags/t1/assets"): None})

    client.untag_assets("t1", ["a1", "a2"])

    assert calls == [("DELETE", "/api/tags/t1/assets", {"ids": ["a1", "a2"]})]


def test_tag_assets_and_untag_assets_skip_the_call_when_theres_nothing_to_send():
    client, calls = _client_with_send({})

    client.tag_assets("t1", [])
    client.untag_assets("t1", [])

    assert calls == []


def test_tagged_assets_pages_through_search_results():
    """Search is paginated; a large tag must not stop at the first page."""
    client = ImmichClient(base_url="http://127.0.0.1:2283", api_key="k")
    seen: list[dict] = []

    def fake_post(path, body):
        seen.append(body)
        if body["page"] == 1:
            return {
                "assets": {
                    "items": [
                        {
                            "id": "a1",
                            "type": "VIDEO",
                            "originalFileName": "one.mov",
                            "fileCreatedAt": "2026-05-04T10:30:00Z",
                        }
                    ],
                    "nextPage": 2,
                }
            }
        return {
            "assets": {
                "items": [
                    {
                        "id": "a2",
                        "type": "VIDEO",
                        "originalFileName": "two.mov",
                        "fileCreatedAt": "2026-05-05T10:30:00Z",
                    }
                ],
                "nextPage": None,
            }
        }

    client._post_json = fake_post  # type: ignore[method-assign]
    assets = client.tagged_assets("tag-1")

    assert [asset.id for asset in assets] == ["a1", "a2"]
    assert [body["page"] for body in seen] == [1, 2]
    # Videos are filtered server-side rather than fetching every photo.
    assert all(body["type"] == "VIDEO" for body in seen)
    assert all(body["tagIds"] == ["tag-1"] for body in seen)


def test_tagged_assets_reports_an_unexpected_payload():
    client = ImmichClient(base_url="http://127.0.0.1:2283", api_key="k")
    client._post_json = lambda path, body: {"unexpected": True}  # type: ignore[method-assign]

    with pytest.raises(ImmichError, match="no assets block"):
        client.tagged_assets("tag-1")
