"""The rating queue serves unrated picks one at a time."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.library.models import Clip, Still, Video

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(username="rater", password="secret123!")


@pytest.fixture
def rater(client, user):
    assert client.login(username="rater", password="secret123!")
    return client


def _video(user, *, name: str, video_type: str = Video.VideoType.TYPE_A) -> Video:
    return Video.objects.create(
        title=name,
        source_path=f"/nakavid/originals/2026/08/26/{name}.mp4",
        video_type=video_type,
        orientation=Video.Orientation.LANDSCAPE,
        recorded_at=timezone.now(),
        duration_seconds=600,
        width=1920,
        height=1080,
        created_by=user,
    )


def _clip(video: Video, *, rating: bool | None = None, score: int = 50) -> Clip:
    index = Clip.objects.count()
    return Clip.objects.create(
        video=video,
        storage_path=f"/nakavid/highlights/2026/08/26/{video.title}__clip_{index:03d}.mp4",
        start_seconds=Decimal(index * 30),
        end_seconds=Decimal(index * 30 + 20),
        highlight_score=score,
        created_by=video.created_by,
        rating=rating,
    )


def _still(video: Video, *, rating: bool | None = None) -> Still:
    index = Still.objects.count()
    return Still.objects.create(
        video=video,
        storage_path=f"/nakavid/highlights/2026/08/26/{video.title}__still_{index:03d}.jpg",
        capture_seconds=Decimal(index * 10),
        quality_score=50,
        created_by=video.created_by,
        rating=rating,
    )


def _queue(client, kind: str = "clips"):
    return client.get(reverse("rating-queue", args=[kind]))


@pytest.mark.django_db
def test_requires_login(client):
    response = _queue(client)

    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")


@pytest.mark.django_db
def test_unknown_kind_is_404(rater):
    assert _queue(rater, "videos").status_code == 404


@pytest.mark.django_db
def test_serves_an_unrated_long_recording_clip_without_its_score(rater, user):
    long_video = _video(user, name="lesson")
    _clip(long_video, rating=True)
    unrated = _clip(long_video, score=87)
    short_video = _video(user, name="moment", video_type=Video.VideoType.TYPE_B)
    _clip(short_video)

    response = _queue(rater)

    assert response.context["item"] == unrated
    content = response.content.decode()
    assert reverse("clip-stream", args=[unrated.pk]) in content
    assert "badge--score" not in content
    assert response.context["rated_up"] == 1
    assert response.context["unrated_count"] == 1


@pytest.mark.django_db
def test_good_rates_the_item_and_moves_on(rater, user):
    video = _video(user, name="lesson")
    first = _clip(video)
    second = _clip(video)
    served = _queue(rater).context["item"]

    response = rater.post(
        reverse("rating-queue-submit", args=["clips", served.pk]), {"rating": "up"}
    )

    assert response.status_code == 302
    served.refresh_from_db()
    assert served.rating is True
    assert _queue(rater).context["item"] == ({first, second} - {served}).pop()


@pytest.mark.django_db
def test_bad_rates_down(rater, user):
    still = _still(_video(user, name="lesson"))

    rater.post(reverse("rating-queue-submit", args=["stills", still.pk]), {"rating": "down"})

    still.refresh_from_db()
    assert still.rating is False


@pytest.mark.django_db
def test_skip_leaves_it_unrated_and_hides_it_until_reset(rater, user):
    still = _still(_video(user, name="lesson"))

    rater.post(reverse("rating-queue-submit", args=["stills", still.pk]), {"rating": "skip"})

    still.refresh_from_db()
    assert still.rating is None
    response = _queue(rater, "stills")
    assert response.context["item"] is None
    assert "Show skipped again" in response.content.decode()

    rater.post(reverse("rating-queue-reset-skipped", args=["stills"]))

    assert _queue(rater, "stills").context["item"] == still


@pytest.mark.django_db
def test_invalid_rating_is_rejected(rater, user):
    clip = _clip(_video(user, name="lesson"))

    response = rater.post(
        reverse("rating-queue-submit", args=["clips", clip.pk]), {"rating": "maybe"}
    )

    assert response.status_code == 400
    clip.refresh_from_db()
    assert clip.rating is None


@pytest.mark.django_db
def test_short_recording_clip_cannot_be_rated_through_the_queue(rater, user):
    clip = _clip(_video(user, name="moment", video_type=Video.VideoType.TYPE_B))

    response = rater.post(reverse("rating-queue-submit", args=["clips", clip.pk]), {"rating": "up"})

    assert response.status_code == 404


@pytest.mark.django_db
def test_order_spreads_across_recordings_not_by_score(rater, user):
    """A score-sorted queue would only ever show the best picks."""
    videos = [_video(user, name=f"lesson{i}") for i in range(4)]
    for video in videos:
        for score in (90, 10):
            _clip(video, score=score)

    served = []
    for _ in range(8):
        item = _queue(rater).context["item"]
        served.append(item)
        rater.post(reverse("rating-queue-submit", args=["clips", item.pk]), {"rating": "up"})

    scores = [clip.highlight_score for clip in served]
    assert scores != sorted(scores, reverse=True)
    assert len({clip.pk for clip in served}) == 8


@pytest.mark.django_db
def test_all_rated_shows_the_report_hint(rater, user):
    _still(_video(user, name="lesson"), rating=True)

    content = _queue(rater, "stills").content.decode()

    assert "Every still is rated" in content
    assert "ratings_report" in content


@pytest.mark.django_db
def test_still_from_short_recording_has_no_lesson_link(rater, user):
    video = _video(user, name="moment", video_type=Video.VideoType.TYPE_B)
    _still(video)

    response = _queue(rater, "stills")

    assert response.status_code == 200
    assert reverse("lesson-view", args=[video.pk]) not in response.content.decode()
