from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.library.models import Clip, Video
from apps.library.storage_paths import (
    build_originals_relative_path,
    parse_originals_relative_path,
    to_absolute_storage_path,
)

User = get_user_model()


@pytest.fixture
def storage_root(tmp_path, settings):
    settings.NAKAVID_STORAGE_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def authenticated_client(client):
    user = User.objects.create_user(username="coach", password="secret123!")
    assert client.login(username="coach", password="secret123!")
    return client, user


def test_build_and_parse_originals_path_round_trip():
    relative_path = build_originals_relative_path(
        recorded_at=date(2026, 7, 7),
        class_name="A",
        theme="Animals",
        filename="crowd_reaction.mp4",
    )

    assert relative_path == "originals/2026/07/20260707_a_animals/crowd_reaction.mp4"

    metadata = parse_originals_relative_path(relative_path)

    assert metadata.recorded_on == date(2026, 7, 7)
    assert metadata.class_name == "a"
    assert metadata.theme == "animals"
    assert metadata.filename == "crowd_reaction.mp4"


@pytest.mark.django_db
def test_type_b_ingest_requires_login(client):
    response = client.get(reverse("type-b-ingest"))

    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")


@pytest.mark.django_db
def test_type_b_ingest_upload_happy_path(authenticated_client, storage_root):
    client, user = authenticated_client
    upload = SimpleUploadedFile(
        "crowd_reaction.mp4",
        b"fake-type-b-video-bytes",
        content_type="video/mp4",
    )

    response = client.post(
        reverse("type-b-ingest"),
        {
            "video_file": upload,
            "class_name": "A",
            "theme": "Animals",
            "recorded_at": "2026-07-07",
        },
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("type-b-ingest")

    relative_path = build_originals_relative_path(
        recorded_at=date(2026, 7, 7),
        class_name="A",
        theme="Animals",
        filename="crowd_reaction.mp4",
    )
    absolute_path = to_absolute_storage_path(storage_root, relative_path)
    saved_file = storage_root / relative_path

    assert saved_file.is_file()
    assert saved_file.read_bytes() == b"fake-type-b-video-bytes"

    video = Video.objects.get()
    clip = Clip.objects.get()

    assert video.title == "crowd_reaction"
    assert video.source_path == absolute_path
    assert video.video_type == Video.VideoType.TYPE_B
    assert video.class_name == "A"
    assert video.theme == "Animals"
    assert video.created_by == user
    assert clip.video == video
    assert clip.storage_path == absolute_path
    assert clip.start_seconds == Decimal("0.000")
    assert clip.end_seconds == Decimal(video.duration_seconds)
    assert clip.created_by == user

    metadata = parse_originals_relative_path(relative_path)
    assert metadata.filename == "crowd_reaction.mp4"


@pytest.mark.django_db
def test_type_b_ingest_get_renders_form(authenticated_client):
    client, _user = authenticated_client

    response = client.get(reverse("type-b-ingest"))

    assert response.status_code == 200
    assert b"Type B ingest" in response.content
    assert b'id="drop-zone"' in response.content


@pytest.mark.django_db
def test_type_b_ingest_rejects_missing_file(authenticated_client, storage_root):
    client, _user = authenticated_client

    response = client.post(
        reverse("type-b-ingest"),
        {
            "class_name": "A",
            "theme": "Animals",
            "recorded_at": "2026-07-07",
        },
    )

    assert response.status_code == 200
    assert Video.objects.count() == 0
    assert Clip.objects.count() == 0
    assert not any(storage_root.rglob("*"))
