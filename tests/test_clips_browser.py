from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.library.models import Clip, Video
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
def sample_clips(db, coach_user, storage_root):
    recorded_a = timezone.make_aware(datetime(2026, 7, 1, 12, 0))
    recorded_b = timezone.make_aware(datetime(2026, 7, 7, 12, 0))

    video_a = Video.objects.create(
        title="lesson_a",
        source_path=to_absolute_storage_path(
            storage_root, "originals/2026/07/20260701_a_animals/lesson_a.mp4"
        ),
        video_type=Video.VideoType.TYPE_B,
        orientation=Video.Orientation.MIXED,
        class_name="A",
        theme="Animals",
        recorded_at=recorded_a,
        duration_seconds=120,
        created_by=coach_user,
    )
    video_b = Video.objects.create(
        title="crowd_reaction",
        source_path=to_absolute_storage_path(
            storage_root, "originals/2026/07/20260707_b_games/crowd_reaction.mp4"
        ),
        video_type=Video.VideoType.TYPE_B,
        orientation=Video.Orientation.MIXED,
        class_name="B",
        theme="Games",
        recorded_at=recorded_b,
        duration_seconds=45,
        created_by=coach_user,
    )

    clip_a = Clip.objects.create(
        video=video_a,
        storage_path=video_a.source_path,
        start_seconds=Decimal("0.000"),
        end_seconds=Decimal("120.000"),
        highlight_score=40,
        created_by=coach_user,
    )
    clip_b = Clip.objects.create(
        video=video_b,
        storage_path=video_b.source_path,
        start_seconds=Decimal("0.000"),
        end_seconds=Decimal("45.000"),
        highlight_score=80,
        created_by=coach_user,
    )
    return clip_a, clip_b


@pytest.mark.django_db
def test_clips_browser_requires_login(client):
    response = client.get(reverse("clips-browser"))

    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")


@pytest.mark.django_db
def test_clips_browser_lists_clips(authenticated_client, sample_clips):
    client, _user = authenticated_client
    clip_a, clip_b = sample_clips

    response = client.get(reverse("clips-browser"))

    assert response.status_code == 200
    content = response.content.decode()
    assert clip_a.video.title in content
    assert clip_b.video.title in content
    assert "Highlight score: 80" in content


@pytest.mark.django_db
def test_clips_browser_filter_by_class(authenticated_client, sample_clips):
    client, _user = authenticated_client
    clip_a, _clip_b = sample_clips

    response = client.get(reverse("clips-browser"), {"class_name": "A"})

    assert response.status_code == 200
    content = response.content.decode()
    assert clip_a.video.title in content
    assert "crowd_reaction" not in content


@pytest.mark.django_db
def test_clips_browser_filter_by_date(authenticated_client, sample_clips):
    client, _user = authenticated_client
    _clip_a, clip_b = sample_clips

    response = client.get(reverse("clips-browser"), {"recorded_date": "2026-07-07"})

    assert response.status_code == 200
    content = response.content.decode()
    assert clip_b.video.title in content
    assert "lesson_a" not in content


@pytest.mark.django_db
def test_clips_browser_filter_by_min_score(authenticated_client, sample_clips):
    client, _user = authenticated_client
    _clip_a, clip_b = sample_clips

    response = client.get(reverse("clips-browser"), {"min_score": "70"})

    assert response.status_code == 200
    content = response.content.decode()
    assert clip_b.video.title in content
    assert "lesson_a" not in content


@pytest.mark.django_db
def test_clip_stream_requires_login(client, sample_clips):
    clip_a, _clip_b = sample_clips

    response = client.get(reverse("clip-stream", args=[clip_a.id]))

    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")


@pytest.mark.django_db
def test_clip_stream_returns_accel_redirect(authenticated_client, sample_clips):
    client, _user = authenticated_client
    clip_a, _clip_b = sample_clips

    response = client.get(reverse("clip-stream", args=[clip_a.id]))

    assert response.status_code == 200
    assert response.content == b""
    assert response["X-Accel-Redirect"] == to_accel_redirect_path(clip_a.storage_path)
    assert response["X-Accel-Redirect"] == ("/originals/2026/07/20260701_a_animals/lesson_a.mp4")


@pytest.mark.django_db
def test_to_accel_redirect_path_strips_nakavid_prefix():
    assert (
        to_accel_redirect_path("/nakavid/originals/2026/07/foo.mp4") == "/originals/2026/07/foo.mp4"
    )
