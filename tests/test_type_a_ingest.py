from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.library.models import Clip, Video
from apps.library.resumable_upload import (
    TUS_RESUMABLE_HEADER,
    ResumableUploadError,
    append_chunk,
    create_upload,
    current_offset,
    finalize_upload,
    load_metadata,
)
from apps.library.storage_paths import (
    build_originals_relative_path,
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


def _create_session(
    storage_root: Path,
    *,
    user_id: int,
    upload_length: int,
    filename: str = "lesson_recording.mp4",
) -> str:
    metadata = create_upload(
        storage_root=storage_root,
        user_id=user_id,
        class_name="A",
        theme="Animals",
        recorded_at=date(2026, 7, 7),
        filename=filename,
        upload_length=upload_length,
    )
    return metadata.upload_id


def test_chunk_assembly_and_finalize(storage_root):
    user_id = 42
    payload = b"part-one-part-two-part-three"
    upload_id = _create_session(storage_root, user_id=user_id, upload_length=len(payload))

    offset = append_chunk(
        storage_root=storage_root,
        upload_id=upload_id,
        user_id=user_id,
        offset=0,
        chunk=b"part-one-",
    )
    assert offset == 9
    assert current_offset(storage_root, upload_id) == 9

    offset = append_chunk(
        storage_root=storage_root,
        upload_id=upload_id,
        user_id=user_id,
        offset=9,
        chunk=b"part-two-",
    )
    assert offset == 18

    offset = append_chunk(
        storage_root=storage_root,
        upload_id=upload_id,
        user_id=user_id,
        offset=18,
        chunk=b"part-three",
    )
    assert offset == len(payload)

    destination, source_path = finalize_upload(
        storage_root=storage_root,
        upload_id=upload_id,
        user_id=user_id,
    )
    assert destination.read_bytes() == payload
    assert source_path == to_absolute_storage_path(
        storage_root,
        build_originals_relative_path(
            recorded_at=date(2026, 7, 7),
            class_name="A",
            theme="Animals",
            filename="lesson_recording.mp4",
        ),
    )
    assert not (storage_root / ".uploads" / upload_id).exists()


def test_resume_rejects_wrong_offset(storage_root):
    user_id = 42
    upload_id = _create_session(storage_root, user_id=user_id, upload_length=20)

    append_chunk(
        storage_root=storage_root,
        upload_id=upload_id,
        user_id=user_id,
        offset=0,
        chunk=b"first-five",
    )

    with pytest.raises(ResumableUploadError) as exc_info:
        append_chunk(
            storage_root=storage_root,
            upload_id=upload_id,
            user_id=user_id,
            offset=0,
            chunk=b"retry",
        )

    assert exc_info.value.status_code == 409
    assert current_offset(storage_root, upload_id) == 10


@pytest.mark.django_db
def test_type_a_ingest_requires_login(client):
    response = client.get(reverse("type-a-ingest"))

    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")


@pytest.mark.django_db
def test_type_a_upload_api_happy_path(authenticated_client, storage_root, monkeypatch):
    client, user = authenticated_client
    monkeypatch.setattr(
        "apps.library.views.probe_duration_seconds",
        lambda _path: 3600,
    )

    payload = b"fake-type-a-video-bytes-for-resume-test"
    create_response = client.post(
        reverse("type-a-upload-create"),
        data=json.dumps(
            {
                "class_name": "A",
                "theme": "Animals",
                "recorded_at": "2026-07-07",
                "filename": "lesson_recording.mp4",
                "upload_length": len(payload),
            }
        ),
        content_type="application/json",
        HTTP_TUS_RESUMABLE=TUS_RESUMABLE_HEADER,
    )

    assert create_response.status_code == 201
    upload_url = create_response["Location"]

    head_response = client.head(
        upload_url,
        HTTP_TUS_RESUMABLE=TUS_RESUMABLE_HEADER,
    )
    assert head_response.status_code == 200
    assert head_response["Upload-Offset"] == "0"

    first_chunk = payload[:10]
    patch_response = client.patch(
        upload_url,
        data=first_chunk,
        content_type="application/offset+octet-stream",
        HTTP_UPLOAD_OFFSET="0",
        HTTP_TUS_RESUMABLE=TUS_RESUMABLE_HEADER,
    )
    assert patch_response.status_code == 204
    assert patch_response["Upload-Offset"] == "10"

    head_response = client.head(
        upload_url,
        HTTP_TUS_RESUMABLE=TUS_RESUMABLE_HEADER,
    )
    assert head_response["Upload-Offset"] == "10"

    second_chunk = payload[10:]
    patch_response = client.patch(
        upload_url,
        data=second_chunk,
        content_type="application/offset+octet-stream",
        HTTP_UPLOAD_OFFSET="10",
        HTTP_TUS_RESUMABLE=TUS_RESUMABLE_HEADER,
    )
    assert patch_response.status_code == 201
    assert patch_response["Upload-Offset"] == str(len(payload))

    relative_path = build_originals_relative_path(
        recorded_at=date(2026, 7, 7),
        class_name="A",
        theme="Animals",
        filename="lesson_recording.mp4",
    )
    saved_file = storage_root / relative_path
    assert saved_file.is_file()
    assert saved_file.read_bytes() == payload

    video = Video.objects.get()
    assert video.title == "lesson_recording"
    assert video.source_path == to_absolute_storage_path(storage_root, relative_path)
    assert video.video_type == Video.VideoType.TYPE_A
    assert video.orientation == Video.Orientation.LANDSCAPE
    assert video.class_name == "A"
    assert video.theme == "Animals"
    assert video.duration_seconds == 3600
    assert video.created_by == user
    assert Clip.objects.count() == 0


@pytest.mark.django_db
def test_type_a_upload_resume_after_interrupt(authenticated_client, storage_root, monkeypatch):
    client, user = authenticated_client
    monkeypatch.setattr(
        "apps.library.views.probe_duration_seconds",
        lambda _path: 5400,
    )

    payload = b"0123456789abcdef" * 4
    create_response = client.post(
        reverse("type-a-upload-create"),
        data=json.dumps(
            {
                "class_name": "B",
                "theme": "Sports",
                "recorded_at": "2026-07-08",
                "filename": "match_day.mp4",
                "upload_length": len(payload),
            }
        ),
        content_type="application/json",
        HTTP_TUS_RESUMABLE=TUS_RESUMABLE_HEADER,
    )
    upload_url = create_response["Location"]

    first_half = payload[: len(payload) // 2]
    client.patch(
        upload_url,
        data=first_half,
        content_type="application/offset+octet-stream",
        HTTP_UPLOAD_OFFSET="0",
        HTTP_TUS_RESUMABLE=TUS_RESUMABLE_HEADER,
    )

    head_response = client.head(
        upload_url,
        HTTP_TUS_RESUMABLE=TUS_RESUMABLE_HEADER,
    )
    resume_offset = int(head_response["Upload-Offset"])
    assert resume_offset == len(first_half)

    second_half = payload[resume_offset:]
    patch_response = client.patch(
        upload_url,
        data=second_half,
        content_type="application/offset+octet-stream",
        HTTP_UPLOAD_OFFSET=str(resume_offset),
        HTTP_TUS_RESUMABLE=TUS_RESUMABLE_HEADER,
    )
    assert patch_response.status_code == 201

    relative_path = build_originals_relative_path(
        recorded_at=date(2026, 7, 8),
        class_name="B",
        theme="Sports",
        filename="match_day.mp4",
    )
    assert (storage_root / relative_path).read_bytes() == payload
    assert Video.objects.filter(video_type=Video.VideoType.TYPE_A).count() == 1
    assert Clip.objects.count() == 0


@pytest.mark.django_db
def test_type_a_ingest_get_renders_form(authenticated_client):
    client, _user = authenticated_client

    response = client.get(reverse("type-a-ingest"))

    assert response.status_code == 200
    assert b"Type A ingest" in response.content
    assert b'id="drop-zone"' in response.content


def test_load_metadata_round_trip(storage_root):
    user_id = 7
    upload_id = _create_session(storage_root, user_id=user_id, upload_length=100)

    metadata = load_metadata(storage_root, upload_id)

    assert metadata.user_id == user_id
    assert metadata.class_name == "A"
    assert metadata.theme == "Animals"
    assert metadata.recorded_on == date(2026, 7, 7)
    assert metadata.safe_filename == "lesson_recording.mp4"
