from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.library.models import Clip, Tag, Video
from apps.library.storage_paths import build_originals_relative_path, to_absolute_storage_path
from apps.pipeline.extraction import select_clip_segments
from apps.pipeline.handlers import handle_clip_extraction
from apps.pipeline.models import Job, ScoringParams

User = get_user_model()


@pytest.fixture
def storage_root(tmp_path, settings):
    settings.NAKAVID_STORAGE_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def user(db):
    return User.objects.create_user(username="extract-test", password="secret123!")


def _create_type_a_video(*, storage_root: Path, user) -> Video:
    relative_path = build_originals_relative_path(
        recorded_at=timezone.now().date(),
        class_name="Kids A",
        theme="Summer Camp",
        filename="lesson.mp4",
    )
    absolute_path = to_absolute_storage_path(storage_root, relative_path)
    file_path = storage_root / relative_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(b"fake-video")
    return Video.objects.create(
        title="Extraction Sample",
        source_path=absolute_path,
        video_type=Video.VideoType.TYPE_A,
        orientation=Video.Orientation.LANDSCAPE,
        class_name="Kids A",
        theme="Summer Camp",
        recorded_at=timezone.now(),
        duration_seconds=120,
        is_private=True,
        created_by=user,
    )


@pytest.mark.django_db
def test_select_clip_segments_prefers_non_overlapping_peaks():
    params = ScoringParams.objects.get()
    params.peak_count = 2
    params.min_gap_seconds = 4
    params.min_clip_length_seconds = 4

    energy_curve = [
        {"start": 0.0, "end": 4.0, "score": 20.0, "signals": {"motion_energy": 0.5}},
        {"start": 8.0, "end": 12.0, "score": 90.0, "signals": {"motion_energy": 0.2}},
        {"start": 10.0, "end": 14.0, "score": 85.0, "signals": {"motion_energy": 0.1}},
        {"start": 28.0, "end": 32.0, "score": 80.0, "signals": {"motion_energy": 0.2}},
    ]

    clips = select_clip_segments(energy_curve=energy_curve, params=params, duration_seconds=40.0)

    assert len(clips) == 2
    assert clips[0].start_seconds < clips[0].end_seconds
    assert clips[1].start_seconds < clips[1].end_seconds
    assert clips[0].end_seconds + float(params.min_gap_seconds) <= clips[1].start_seconds


@pytest.mark.django_db
def test_handle_clip_extraction_creates_highlight_rows_and_inherits_tags(storage_root, user):
    video = _create_type_a_video(storage_root=storage_root, user=user)
    tag = Tag.objects.create(slug="warmup", label="Warmup")
    video.tags.add(tag)
    placeholder = Clip.objects.create(
        video=video,
        storage_path=video.source_path,
        start_seconds=Decimal("0.000"),
        end_seconds=Decimal("120.000"),
        highlight_score=93,
        energy_curve=[
            {"start": 8.0, "end": 12.0, "score": 90.0, "signals": {"motion_energy": 0.2}},
            {"start": 28.0, "end": 32.0, "score": 80.0, "signals": {"motion_energy": 0.2}},
        ],
        created_by=user,
    )
    params = ScoringParams.objects.get()
    job = Job.objects.create(
        video=video,
        job_type=Job.JobType.CLIP_EXTRACTION,
        status=Job.Status.PROCESSING,
        scoring_params=params,
    )

    with (
        patch("apps.pipeline.handlers.run_ffmpeg_trim") as run_trim,
        patch("apps.pipeline.handlers.run_ffmpeg_thumbnail") as run_thumbnail,
    ):
        handle_clip_extraction(job)

    clips = list(Clip.objects.filter(video=video).order_by("storage_path"))
    assert len(clips) == 2
    assert not Clip.objects.filter(pk=placeholder.pk).exists()
    assert run_trim.call_count == 2
    assert run_thumbnail.call_count == 2
    for index, clip in enumerate(clips, start=1):
        assert clip.storage_path.startswith("/nakavid/highlights/")
        assert clip.storage_path.endswith(f"__clip_{index:03d}.mp4")
        assert clip.thumbnail_path.endswith(f"__clip_{index:03d}.jpg")
        assert clip.energy_curve
        assert list(clip.tags.values_list("slug", flat=True)) == ["warmup"]
