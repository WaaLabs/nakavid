from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.library.models import Clip, Tag, Video
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
def library_items(db, coach_user, storage_root):
    recorded = timezone.make_aware(datetime(2026, 7, 1, 12, 0))
    video_a = Video.objects.create(
        title="full_lesson",
        source_path=to_absolute_storage_path(
            storage_root, "originals/2026/07/20260701_a_animals/full_lesson.mp4"
        ),
        video_type=Video.VideoType.TYPE_A,
        orientation=Video.Orientation.LANDSCAPE,
        class_name="A",
        theme="Animals",
        recorded_at=recorded,
        duration_seconds=5400,
        created_by=coach_user,
    )
    video_b = Video.objects.create(
        title="quick_clip_source",
        source_path=to_absolute_storage_path(
            storage_root, "originals/2026/07/20260701_b_games/quick.mp4"
        ),
        video_type=Video.VideoType.TYPE_B,
        orientation=Video.Orientation.MIXED,
        class_name="B",
        theme="Games",
        recorded_at=recorded,
        duration_seconds=45,
        created_by=coach_user,
    )
    clip_a = Clip.objects.create(
        video=video_a,
        storage_path=to_absolute_storage_path(
            storage_root, "highlights/2026/07/20260701_a_animals/clip_001.mp4"
        ),
        start_seconds=Decimal("0.000"),
        end_seconds=Decimal("5.000"),
        highlight_score=70,
        created_by=coach_user,
    )
    clip_b = Clip.objects.create(
        video=video_b,
        storage_path=video_b.source_path,
        start_seconds=Decimal("0.000"),
        end_seconds=Decimal("45.000"),
        highlight_score=40,
        created_by=coach_user,
    )
    tag_warmup = Tag.objects.create(slug="warmup", label="Warm-up")
    tag_animals = Tag.objects.create(slug="animals", label="Animals")
    tag_games = Tag.objects.create(slug="games", label="Games")
    return {
        "video_a": video_a,
        "video_b": video_b,
        "clip_a": clip_a,
        "clip_b": clip_b,
        "tag_warmup": tag_warmup,
        "tag_animals": tag_animals,
        "tag_games": tag_games,
    }


@pytest.mark.django_db
def test_bulk_tagging_requires_login(client):
    response = client.get(reverse("bulk-tagging"))

    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")


@pytest.mark.django_db
def test_bulk_tagging_page_lists_items(authenticated_client, library_items):
    client, _user = authenticated_client

    response = client.get(reverse("bulk-tagging"))

    assert response.status_code == 200
    assert b"full_lesson" in response.content
    assert b"Warm-up" in response.content
    assert b"Apply tags" in response.content


@pytest.mark.django_db
def test_bulk_add_tags_to_videos_and_clips_preserves_existing(authenticated_client, library_items):
    client, _user = authenticated_client
    video_a = library_items["video_a"]
    clip_a = library_items["clip_a"]
    clip_b = library_items["clip_b"]
    tag_warmup = library_items["tag_warmup"]
    tag_animals = library_items["tag_animals"]
    tag_games = library_items["tag_games"]

    video_a.tags.add(tag_warmup)
    clip_a.tags.add(tag_warmup)

    response = client.post(
        reverse("bulk-tagging"),
        {
            "videos": [str(video_a.id)],
            "clips": [str(clip_a.id), str(clip_b.id)],
            "tags": [str(tag_animals.id), str(tag_games.id)],
            "action": "add",
        },
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("bulk-tagging")

    assert set(video_a.tags.values_list("slug", flat=True)) == {
        "warmup",
        "animals",
        "games",
    }
    assert set(clip_a.tags.values_list("slug", flat=True)) == {
        "warmup",
        "animals",
        "games",
    }
    assert set(clip_b.tags.values_list("slug", flat=True)) == {"animals", "games"}
    assert set(library_items["video_b"].tags.values_list("slug", flat=True)) == set()


@pytest.mark.django_db
def test_bulk_remove_tags_only_clears_selected(authenticated_client, library_items):
    client, _user = authenticated_client
    video_a = library_items["video_a"]
    clip_a = library_items["clip_a"]
    tag_warmup = library_items["tag_warmup"]
    tag_animals = library_items["tag_animals"]
    tag_games = library_items["tag_games"]

    video_a.tags.set([tag_warmup, tag_animals, tag_games])
    clip_a.tags.set([tag_warmup, tag_animals])

    response = client.post(
        reverse("bulk-tagging"),
        {
            "videos": [str(video_a.id)],
            "clips": [str(clip_a.id)],
            "tags": [str(tag_animals.id)],
            "action": "remove",
        },
    )

    assert response.status_code == 302
    assert set(video_a.tags.values_list("slug", flat=True)) == {"warmup", "games"}
    assert set(clip_a.tags.values_list("slug", flat=True)) == {"warmup"}


@pytest.mark.django_db
def test_bulk_tagging_requires_selection_and_tags(authenticated_client, library_items):
    client, _user = authenticated_client
    tag_warmup = library_items["tag_warmup"]
    video_a = library_items["video_a"]

    missing_targets = client.post(
        reverse("bulk-tagging"),
        {
            "tags": [str(tag_warmup.id)],
            "action": "add",
        },
    )
    assert missing_targets.status_code == 200
    assert b"Select at least one video or clip." in missing_targets.content
    assert video_a.tags.count() == 0

    missing_tags = client.post(
        reverse("bulk-tagging"),
        {
            "videos": [str(video_a.id)],
            "action": "add",
        },
    )
    assert missing_tags.status_code == 200
    assert video_a.tags.count() == 0
