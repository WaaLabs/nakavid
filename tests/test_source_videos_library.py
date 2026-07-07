from __future__ import annotations

from datetime import datetime

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.library.models import Video
from apps.library.storage_paths import to_absolute_storage_path, to_accel_redirect_path

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
def sample_videos(db, coach_user, storage_root):
    recorded_a = timezone.make_aware(datetime(2026, 7, 1, 12, 0))
    recorded_b = timezone.make_aware(datetime(2026, 7, 7, 12, 0))

    type_a = Video.objects.create(
        title="full_lesson",
        source_path=to_absolute_storage_path(
            storage_root, "originals/2026/07/20260701_a_animals/full_lesson.mp4"
        ),
        video_type=Video.VideoType.TYPE_A,
        orientation=Video.Orientation.LANDSCAPE,
        class_name="A",
        theme="Animals",
        recorded_at=recorded_a,
        duration_seconds=5400,
        created_by=coach_user,
    )
    type_b = Video.objects.create(
        title="quick_clip",
        source_path=to_absolute_storage_path(
            storage_root, "originals/2026/07/20260707_b_games/quick_clip.mp4"
        ),
        video_type=Video.VideoType.TYPE_B,
        orientation=Video.Orientation.MIXED,
        class_name="B",
        theme="Games",
        recorded_at=recorded_b,
        duration_seconds=45,
        created_by=coach_user,
    )
    return type_a, type_b


@pytest.mark.django_db
def test_source_videos_requires_login(client):
    response = client.get(reverse("source-videos"))

    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")


@pytest.mark.django_db
def test_source_videos_lists_type_a_only(authenticated_client, sample_videos):
    client, _user = authenticated_client
    type_a, type_b = sample_videos

    response = client.get(reverse("source-videos"))

    assert response.status_code == 200
    content = response.content.decode()
    assert type_a.title in content
    assert type_b.title not in content
    assert "Class: A" in content
    assert "Theme: Animals" in content
    assert "Duration: 1:30:00" in content


@pytest.mark.django_db
def test_source_videos_filter_by_class(authenticated_client, sample_videos):
    client, _user = authenticated_client
    type_a, _type_b = sample_videos

    response = client.get(reverse("source-videos"), {"class_name": "A"})

    assert response.status_code == 200
    content = response.content.decode()
    assert type_a.title in content
    assert "quick_clip" not in content


@pytest.mark.django_db
def test_source_videos_filter_by_date(
    authenticated_client, sample_videos, coach_user, storage_root
):
    client, _user = authenticated_client
    type_a, _type_b = sample_videos
    other_date = timezone.make_aware(datetime(2026, 6, 15, 12, 0))
    Video.objects.create(
        title="june_lesson",
        source_path=to_absolute_storage_path(
            storage_root, "originals/2026/06/20260615_c_colors/june_lesson.mp4"
        ),
        video_type=Video.VideoType.TYPE_A,
        orientation=Video.Orientation.LANDSCAPE,
        class_name="C",
        theme="Colors",
        recorded_at=other_date,
        duration_seconds=3600,
        created_by=coach_user,
    )

    response = client.get(reverse("source-videos"), {"recorded_date": "2026-07-01"})

    assert response.status_code == 200
    content = response.content.decode()
    assert type_a.title in content
    assert "june_lesson" not in content


@pytest.mark.django_db
def test_video_stream_requires_login(client, sample_videos):
    type_a, _type_b = sample_videos

    response = client.get(reverse("video-stream", args=[type_a.id]))

    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")


@pytest.mark.django_db
def test_video_stream_returns_accel_redirect(authenticated_client, sample_videos):
    client, _user = authenticated_client
    type_a, _type_b = sample_videos

    response = client.get(reverse("video-stream", args=[type_a.id]))

    assert response.status_code == 200
    assert response.content == b""
    assert response["X-Accel-Redirect"] == to_accel_redirect_path(type_a.source_path)
    assert response["X-Accel-Redirect"] == ("/originals/2026/07/20260701_a_animals/full_lesson.mp4")


@pytest.mark.django_db
def test_video_stream_rejects_type_b(authenticated_client, sample_videos):
    client, _user = authenticated_client
    _type_a, type_b = sample_videos

    response = client.get(reverse("video-stream", args=[type_b.id]))

    assert response.status_code == 404
