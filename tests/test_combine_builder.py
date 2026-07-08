from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.library.models import Clip, Combine, Video
from apps.library.storage_paths import to_absolute_storage_path
from apps.pipeline.models import Job

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
def library_clips(db, coach_user, storage_root):
    recorded_at = timezone.make_aware(datetime(2026, 7, 1, 12, 0))
    video = Video.objects.create(
        title="combine_source",
        source_path=to_absolute_storage_path(
            storage_root, "originals/2026/07/20260701_a_animals/combine_source.mp4"
        ),
        video_type=Video.VideoType.TYPE_A,
        orientation=Video.Orientation.LANDSCAPE,
        class_name="A",
        theme="Animals",
        recorded_at=recorded_at,
        duration_seconds=600,
        created_by=coach_user,
    )
    first = Clip.objects.create(
        video=video,
        storage_path=to_absolute_storage_path(
            storage_root, "highlights/2026/07/20260701_a_animals/combine_source__clip_001.mp4"
        ),
        start_seconds=Decimal("10.000"),
        end_seconds=Decimal("40.000"),
        highlight_score=80,
        created_by=coach_user,
    )
    second = Clip.objects.create(
        video=video,
        storage_path=to_absolute_storage_path(
            storage_root, "highlights/2026/07/20260701_a_animals/combine_source__clip_002.mp4"
        ),
        start_seconds=Decimal("120.000"),
        end_seconds=Decimal("150.000"),
        highlight_score=55,
        created_by=coach_user,
    )
    return first, second


@pytest.mark.django_db
def test_combine_builder_requires_login(client, library_clips):
    response = client.get(reverse("combine-builder"))

    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")


@pytest.mark.django_db
def test_combine_builder_submit_requires_login(client, library_clips):
    first, second = library_clips

    response = client.post(
        reverse("combine-builder-submit"),
        data=json.dumps({"title": "Week 1", "clip_ids": [first.id, second.id]}),
        content_type="application/json",
    )

    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")


@pytest.mark.django_db
def test_combine_builder_renders_island_props(authenticated_client, library_clips):
    client, _user = authenticated_client
    first, second = library_clips

    response = client.get(reverse("combine-builder"))

    assert response.status_code == 200
    content = response.content.decode()
    assert 'id="combine-builder-island"' in content
    assert 'src="/static/islands/drag-combine.js"' in content
    assert reverse("combine-builder-submit") in content

    marker = 'data-clips="'
    start = content.index(marker) + len(marker)
    end = content.index('"', start)
    clips = json.loads(content[start:end].replace("&quot;", '"'))
    assert [clip["id"] for clip in clips] == [first.id, second.id]
    assert clips[0]["streamUrl"].endswith(reverse("clip-stream", args=[first.id]))
    assert clips[0]["durationSeconds"] == 30.0


@pytest.mark.django_db
def test_combine_builder_submit_creates_combine_and_job(authenticated_client, library_clips):
    client, user = authenticated_client
    first, second = library_clips

    response = client.post(
        reverse("combine-builder-submit"),
        data=json.dumps({"title": "Week 1 Highlights", "clip_ids": [second.id, first.id]}),
        content_type="application/json",
    )

    assert response.status_code == 201
    payload = response.json()
    combine = Combine.objects.get(pk=payload["combineId"])
    assert combine.title == "Week 1 Highlights"
    assert combine.created_by_id == user.id
    assert list(combine.combine_clips.values_list("clip_id", flat=True)) == [
        second.id,
        first.id,
    ]

    job = Job.objects.get(pk=payload["jobId"])
    assert job.job_type == Job.JobType.COMBINE_EXPORT
    assert job.combine_id == combine.id
    assert job.video_id == second.video_id
    assert job.status == Job.Status.PENDING


@pytest.mark.django_db
def test_combine_builder_submit_rejects_empty_clip_list(authenticated_client):
    client, _user = authenticated_client

    response = client.post(
        reverse("combine-builder-submit"),
        data=json.dumps({"title": "Empty", "clip_ids": []}),
        content_type="application/json",
    )

    assert response.status_code == 400
    assert "clip_ids" in response.json()["errors"]
