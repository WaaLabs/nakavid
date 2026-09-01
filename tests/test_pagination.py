"""Browse pages were unbounded, and each clip card renders a <video>."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.library.models import Clip, Video
from apps.library.views import PAGE_SIZE

User = get_user_model()


@pytest.fixture
def storage_root(tmp_path, settings):
    settings.NAKAVID_STORAGE_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def user(db):
    return User.objects.create_user(username="browser", password="secret123!")


@pytest.fixture
def logged_in(client, user):
    assert client.login(username="browser", password="secret123!")
    return client


def _video(*, user, title, class_name="A"):
    return Video.objects.create(
        title=title,
        source_path=f"/nakavid/originals/2026/08/20260826_a_b/{title}.mp4",
        video_type=Video.VideoType.TYPE_A,
        orientation=Video.Orientation.LANDSCAPE,
        class_name=class_name,
        theme="B",
        recorded_at=timezone.now(),
        duration_seconds=600,
        is_private=True,
        created_by=user,
    )


def _clips(*, user, count, class_name="A"):
    video = _video(user=user, title=f"lesson_{class_name}", class_name=class_name)
    return [
        Clip.objects.create(
            video=video,
            storage_path=f"/nakavid/highlights/2026/08/{class_name}/clip_{index:03d}.mp4",
            start_seconds=Decimal(index * 10),
            end_seconds=Decimal(index * 10 + 8),
            highlight_score=50,
            created_by=user,
        )
        for index in range(count)
    ]


@pytest.mark.django_db
def test_clips_page_is_bounded(logged_in, user, storage_root):
    _clips(user=user, count=PAGE_SIZE + 10)

    response = logged_in.get(reverse("clips-browser"))

    assert len(response.context["clips"]) == PAGE_SIZE
    assert response.context["page"].paginator.count == PAGE_SIZE + 10
    assert response.context["page"].has_next is True


@pytest.mark.django_db
def test_second_page_holds_the_remainder(logged_in, user, storage_root):
    _clips(user=user, count=PAGE_SIZE + 5)

    response = logged_in.get(reverse("clips-browser"), {"page": 2})

    assert len(response.context["clips"]) == 5
    assert response.context["page"].has_next is False


@pytest.mark.django_db
def test_paging_keeps_the_active_filters(logged_in, user, storage_root):
    """Paging must not quietly reset a filter and show everything."""
    _clips(user=user, count=PAGE_SIZE + 5, class_name="Quokka")
    _clips(user=user, count=3, class_name="Other")

    response = logged_in.get(reverse("clips-browser"), {"class_name": "Quokka", "page": 2})

    assert response.context["page"].paginator.count == PAGE_SIZE + 5
    # The link back to page 1 carries the filter with it.
    assert "class_name=Quokka" in response.context["querystring"]
    assert "page=" not in response.context["querystring"]


@pytest.mark.django_db
def test_an_out_of_range_page_does_not_error(logged_in, user, storage_root):
    _clips(user=user, count=3)

    response = logged_in.get(reverse("clips-browser"), {"page": 99})

    assert response.status_code == 200
    assert response.context["page"].number == 1


@pytest.mark.django_db
def test_a_nonsense_page_does_not_error(logged_in, user, storage_root):
    _clips(user=user, count=3)

    assert logged_in.get(reverse("clips-browser"), {"page": "banana"}).status_code == 200


@pytest.mark.django_db
def test_recordings_page_is_bounded(logged_in, user, storage_root):
    for index in range(PAGE_SIZE + 4):
        _video(user=user, title=f"rec_{index:03d}")

    response = logged_in.get(reverse("source-videos"))

    assert len(response.context["video_rows"]) == PAGE_SIZE
    assert response.context["page"].paginator.count == PAGE_SIZE + 4


@pytest.mark.django_db
def test_combines_page_is_bounded(logged_in, user, storage_root):
    from apps.library.models import Combine

    for index in range(PAGE_SIZE + 2):
        Combine.objects.create(title=f"combine {index}", created_by=user)

    response = logged_in.get(reverse("combines"))

    assert len(response.context["combines"]) == PAGE_SIZE
    assert response.context["page"].paginator.count == PAGE_SIZE + 2
