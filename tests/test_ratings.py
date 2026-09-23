from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.library.models import Clip, Still, Video
from apps.library.storage_paths import to_absolute_storage_path

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
    return Video.objects.create(
        title="lesson_a",
        source_path=to_absolute_storage_path(
            storage_root, "originals/2026/07/20260701_a_animals/lesson_a.mp4"
        ),
        video_type=Video.VideoType.TYPE_A,
        orientation=Video.Orientation.LANDSCAPE,
        recorded_at=timezone.now(),
        duration_seconds=120,
        created_by=coach_user,
    )


@pytest.fixture
def sample_clip(sample_video, coach_user, storage_root):
    return Clip.objects.create(
        video=sample_video,
        storage_path=to_absolute_storage_path(
            storage_root, "highlights/2026/07/20260701_a_animals/lesson_a__clip_001.mp4"
        ),
        start_seconds=Decimal("0.000"),
        end_seconds=Decimal("30.000"),
        highlight_score=70,
        created_by=coach_user,
    )


@pytest.fixture
def sample_still(sample_video, coach_user, storage_root):
    return Still.objects.create(
        video=sample_video,
        storage_path=to_absolute_storage_path(
            storage_root, "highlights/2026/07/20260701_a_animals/lesson_a__still_001__p1.jpg"
        ),
        capture_seconds=Decimal("42.000"),
        quality_score=77,
        created_by=coach_user,
    )


@pytest.mark.django_db
def test_rate_clip_requires_login(client, sample_clip):
    response = client.post(reverse("rate-clip", args=[sample_clip.id]), {"rating": "up"})

    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")


@pytest.mark.django_db
def test_rate_clip_up_then_clear(authenticated_client, sample_clip):
    client, _user = authenticated_client
    url = reverse("rate-clip", args=[sample_clip.id])

    response = client.post(url, {"rating": "up"})
    assert response.status_code == 200
    assert response.json() == {"rating": True}
    sample_clip.refresh_from_db()
    assert sample_clip.rating is True

    response = client.post(url, {"rating": "clear"})
    assert response.status_code == 200
    assert response.json() == {"rating": None}
    sample_clip.refresh_from_db()
    assert sample_clip.rating is None


@pytest.mark.django_db
def test_rate_clip_down(authenticated_client, sample_clip):
    client, _user = authenticated_client

    response = client.post(reverse("rate-clip", args=[sample_clip.id]), {"rating": "down"})

    assert response.status_code == 200
    assert response.json() == {"rating": False}
    sample_clip.refresh_from_db()
    assert sample_clip.rating is False


@pytest.mark.django_db
def test_rate_clip_rejects_invalid_value(authenticated_client, sample_clip):
    client, _user = authenticated_client

    response = client.post(reverse("rate-clip", args=[sample_clip.id]), {"rating": "sideways"})

    assert response.status_code == 400
    sample_clip.refresh_from_db()
    assert sample_clip.rating is None


@pytest.mark.django_db
def test_rate_clip_requires_post(authenticated_client, sample_clip):
    client, _user = authenticated_client

    response = client.get(reverse("rate-clip", args=[sample_clip.id]))

    assert response.status_code == 405


@pytest.mark.django_db
def test_rate_still_requires_login(client, sample_still):
    response = client.post(reverse("rate-still", args=[sample_still.id]), {"rating": "up"})

    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")


@pytest.mark.django_db
def test_rate_still_up_then_down(authenticated_client, sample_still):
    client, _user = authenticated_client
    url = reverse("rate-still", args=[sample_still.id])

    response = client.post(url, {"rating": "up"})
    assert response.json() == {"rating": True}

    response = client.post(url, {"rating": "down"})
    assert response.status_code == 200
    assert response.json() == {"rating": False}
    sample_still.refresh_from_db()
    assert sample_still.rating is False


@pytest.mark.django_db
def test_clips_browser_renders_rating_buttons(authenticated_client, sample_clip):
    client, _user = authenticated_client

    response = client.get(reverse("clips-browser"))

    content = response.content.decode()
    rate_url = reverse("rate-clip", args=[sample_clip.id])
    assert f'data-rate-url="{rate_url}"' in content
    assert 'data-value="up"' in content
    assert 'data-value="down"' in content


@pytest.mark.django_db
def test_stills_browser_renders_rating_buttons(authenticated_client, sample_still):
    client, _user = authenticated_client

    response = client.get(reverse("stills-browser"))

    content = response.content.decode()
    rate_url = reverse("rate-still", args=[sample_still.id])
    assert f'data-rate-url="{rate_url}"' in content
