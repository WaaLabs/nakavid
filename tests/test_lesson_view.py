from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.library.models import Clip, Video
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
def type_a_with_clips(db, coach_user, storage_root):
    recorded_at = timezone.make_aware(datetime(2026, 7, 1, 12, 0))
    video = Video.objects.create(
        title="timeline_lesson",
        source_path=to_absolute_storage_path(
            storage_root, "originals/2026/07/20260701_a_animals/timeline_lesson.mp4"
        ),
        video_type=Video.VideoType.TYPE_A,
        orientation=Video.Orientation.LANDSCAPE,
        class_name="A",
        theme="Animals",
        recorded_at=recorded_at,
        duration_seconds=600,
        created_by=coach_user,
    )
    early = Clip.objects.create(
        video=video,
        storage_path=to_absolute_storage_path(
            storage_root, "highlights/2026/07/20260701_a_animals/timeline_lesson__clip_001.mp4"
        ),
        start_seconds=Decimal("30.000"),
        end_seconds=Decimal("60.000"),
        highlight_score=72,
        created_by=coach_user,
    )
    late = Clip.objects.create(
        video=video,
        storage_path=to_absolute_storage_path(
            storage_root, "highlights/2026/07/20260701_a_animals/timeline_lesson__clip_002.mp4"
        ),
        start_seconds=Decimal("240.500"),
        end_seconds=Decimal("300.000"),
        highlight_score=41,
        created_by=coach_user,
    )
    return video, early, late


@pytest.mark.django_db
def test_lesson_view_requires_login(client, type_a_with_clips):
    video, _early, _late = type_a_with_clips

    response = client.get(reverse("lesson-view", args=[video.id]))

    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")


@pytest.mark.django_db
def test_lesson_view_rejects_type_b(authenticated_client, coach_user, storage_root):
    client, _user = authenticated_client
    recorded_at = timezone.make_aware(datetime(2026, 7, 7, 12, 0))
    type_b = Video.objects.create(
        title="phone_clip",
        source_path=to_absolute_storage_path(
            storage_root, "originals/2026/07/20260707_b_games/phone_clip.mp4"
        ),
        video_type=Video.VideoType.TYPE_B,
        orientation=Video.Orientation.MIXED,
        class_name="B",
        theme="Games",
        recorded_at=recorded_at,
        duration_seconds=45,
        created_by=coach_user,
    )

    response = client.get(reverse("lesson-view", args=[type_b.id]))

    assert response.status_code == 404


@pytest.mark.django_db
def test_lesson_view_renders_island_props(authenticated_client, type_a_with_clips):
    client, _user = authenticated_client
    video, early, late = type_a_with_clips

    response = client.get(reverse("lesson-view", args=[video.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert video.title in content
    assert 'id="lesson-timeline-island"' in content
    assert 'id="lesson-source-player"' in content
    assert f'data-duration-seconds="{video.duration_seconds}"' in content
    assert 'src="/static/islands/timeline-scrub.js"' in content
    assert reverse("video-stream", args=[video.id]) in content

    marker = 'data-clips="'
    start = content.index(marker) + len(marker)
    end = content.index('"', start)
    clips_attr = content[start:end]
    clips = json.loads(clips_attr.replace("&quot;", '"'))
    assert clips == [
        {
            "id": early.id,
            "startSeconds": 30.0,
            "endSeconds": 60.0,
            "highlightScore": 72,
            "label": "30.0s–60.0s",
        },
        {
            "id": late.id,
            "startSeconds": 240.5,
            "endSeconds": 300.0,
            "highlightScore": 41,
            "label": "240.5s–300.0s",
        },
    ]


@pytest.mark.django_db
def test_source_videos_links_to_lesson_view(authenticated_client, type_a_with_clips):
    client, _user = authenticated_client
    video, _early, _late = type_a_with_clips

    response = client.get(reverse("source-videos"))

    assert response.status_code == 200
    assert reverse("lesson-view", args=[video.id]) in response.content.decode()
