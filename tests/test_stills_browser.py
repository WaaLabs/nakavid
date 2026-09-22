from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.library.models import Still, Video
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
def sample_still(db, coach_user, storage_root):
    video = Video.objects.create(
        title="lesson_a",
        source_path=to_absolute_storage_path(
            storage_root, "originals/2026/07/20260701_a_animals/lesson_a.mp4"
        ),
        video_type=Video.VideoType.TYPE_A,
        orientation=Video.Orientation.LANDSCAPE,
        class_name="A",
        theme="Animals",
        recorded_at=timezone.make_aware(datetime(2026, 7, 1, 12, 0)),
        duration_seconds=120,
        created_by=coach_user,
    )
    still = Still.objects.create(
        video=video,
        storage_path=to_absolute_storage_path(
            storage_root, "highlights/2026/07/20260701_a_animals/lesson_a__still_001__p1.jpg"
        ),
        capture_seconds=Decimal("42.000"),
        quality_score=77,
        created_by=coach_user,
    )
    return still


@pytest.mark.django_db
def test_stills_browser_requires_login(client):
    response = client.get(reverse("stills-browser"))

    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")


@pytest.mark.django_db
def test_stills_browser_lists_stills(authenticated_client, sample_still):
    client, _user = authenticated_client

    response = client.get(reverse("stills-browser"))

    assert response.status_code == 200
    content = response.content.decode()
    assert sample_still.video.title in content
    assert ">77</span>" in content
    assert "0:42" in content


@pytest.mark.django_db
def test_stills_browser_renders_lightbox_trigger_and_dialog(authenticated_client, sample_still):
    """Clicking a still opens a larger view rather than doing nothing."""
    client, _user = authenticated_client

    response = client.get(reverse("stills-browser"))

    assert response.status_code == 200
    content = response.content.decode()
    still_url = reverse("still-image", args=[sample_still.id])
    assert f'data-lightbox-src="{still_url}"' in content
    assert 'id="still-lightbox"' in content
    assert 'src="/static/js/stills-lightbox.js"' in content


@pytest.mark.django_db
def test_stills_browser_empty_state(authenticated_client):
    client, _user = authenticated_client

    response = client.get(reverse("stills-browser"))

    assert response.status_code == 200
    assert "No stills yet." in response.content.decode()


@pytest.mark.django_db
def test_still_image_requires_login(client, sample_still):
    response = client.get(reverse("still-image", args=[sample_still.id]))

    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")


@pytest.mark.django_db
def test_still_image_returns_accel_redirect(authenticated_client, sample_still):
    client, _user = authenticated_client

    response = client.get(reverse("still-image", args=[sample_still.id]))

    assert response.status_code == 200
    assert response.content == b""
    assert response["X-Accel-Redirect"] == to_accel_redirect_path(sample_still.storage_path)
