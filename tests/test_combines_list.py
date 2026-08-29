"""Combines were a dead end: exported by the worker, reachable by nobody."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.library.models import Clip, Combine, CombineClip, Video
from apps.library.storage_paths import to_accel_redirect_path

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(username="editor", password="secret123!")


@pytest.fixture
def clip(user):
    video = Video.objects.create(
        title="lesson",
        source_path="/nakavid/originals/2026/08/20260826_a_b/lesson.mp4",
        video_type=Video.VideoType.TYPE_A,
        orientation=Video.Orientation.LANDSCAPE,
        class_name="A",
        theme="B",
        recorded_at=timezone.now(),
        duration_seconds=600,
        is_private=True,
        created_by=user,
    )
    return Clip.objects.create(
        video=video,
        storage_path="/nakavid/highlights/2026/08/20260826_a_b/lesson__clip_001.mp4",
        start_seconds=Decimal("10.000"),
        end_seconds=Decimal("40.000"),
        highlight_score=70,
        created_by=user,
    )


def _combine(*, user, clip, title, status, output=""):
    combine = Combine.objects.create(
        title=title, status=status, output_path=output, created_by=user
    )
    CombineClip.objects.create(combine=combine, clip=clip, position=1)
    return combine


@pytest.mark.django_db
def test_combines_list_requires_login(client, user):
    response = client.get(reverse("combines"))

    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")


@pytest.mark.django_db
def test_combines_list_shows_every_state(client, user, clip):
    done = _combine(
        user=user,
        clip=clip,
        title="Sports Day",
        status=Combine.Status.DONE,
        output="/nakavid/combines/sports-day_20260826.mp4",
    )
    pending = _combine(user=user, clip=clip, title="Still Going", status=Combine.Status.PENDING)
    failed = _combine(user=user, clip=clip, title="Broke", status=Combine.Status.ERROR)

    assert client.login(username="editor", password="secret123!")
    response = client.get(reverse("combines"))

    assert response.status_code == 200
    content = response.content.decode()
    for combine in (done, pending, failed):
        assert combine.title in content
    # Only the finished one offers its file.
    assert reverse("combine-output", args=[done.pk]) in content
    assert reverse("combine-output", args=[pending.pk]) not in content


@pytest.mark.django_db
def test_combine_output_hands_off_to_the_proxy(client, user, clip):
    done = _combine(
        user=user,
        clip=clip,
        title="Sports Day",
        status=Combine.Status.DONE,
        output="/nakavid/combines/sports-day_20260826.mp4",
    )

    assert client.login(username="editor", password="secret123!")
    response = client.get(reverse("combine-output", args=[done.pk]))

    assert response.status_code == 200
    assert response.content == b""
    assert response["X-Accel-Redirect"] == to_accel_redirect_path(done.output_path)


@pytest.mark.django_db
def test_combine_output_404s_before_the_export_finishes(client, user, clip):
    pending = _combine(user=user, clip=clip, title="Still Going", status=Combine.Status.PENDING)

    assert client.login(username="editor", password="secret123!")

    assert client.get(reverse("combine-output", args=[pending.pk])).status_code == 404


@pytest.mark.django_db
def test_combine_output_requires_login(client, user, clip):
    done = _combine(
        user=user,
        clip=clip,
        title="Sports Day",
        status=Combine.Status.DONE,
        output="/nakavid/combines/sports-day_20260826.mp4",
    )

    response = client.get(reverse("combine-output", args=[done.pk]))

    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")
