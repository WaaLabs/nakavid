from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.library.models import Still, Tag, Video
from apps.library.storage_paths import build_originals_relative_path, to_absolute_storage_path
from apps.pipeline.handlers import handle_still_extraction
from apps.pipeline.models import Job, ScoringParams
from apps.pipeline.stills import StillCandidate

User = get_user_model()


@pytest.fixture
def storage_root(tmp_path, settings):
    settings.NAKAVID_STORAGE_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def user(db):
    return User.objects.create_user(username="stills-test", password="secret123!")


def _create_type_a_video(*, storage_root: Path, user) -> Video:
    return _create_video(
        storage_root=storage_root,
        user=user,
        filename="lesson.mp4",
        video_type=Video.VideoType.TYPE_A,
        orientation=Video.Orientation.LANDSCAPE,
        duration_seconds=60,
    )


def _create_video(
    *,
    storage_root: Path,
    user,
    filename: str,
    video_type: str,
    orientation: str,
    duration_seconds: float,
) -> Video:
    relative_path = build_originals_relative_path(
        recorded_at=timezone.now().date(),
        filename=filename,
    )
    absolute_path = to_absolute_storage_path(storage_root, relative_path)
    file_path = storage_root / relative_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(b"fake-video")
    return Video.objects.create(
        title="Stills Sample",
        source_path=absolute_path,
        video_type=video_type,
        orientation=orientation,
        recorded_at=timezone.now(),
        duration_seconds=duration_seconds,
        is_private=True,
        created_by=user,
    )


def _candidates() -> list[StillCandidate]:
    return [
        StillCandidate(
            at_seconds=10.0,
            quality_score=88.0,
            face_count=1,
            smile_count=1,
            sharpness=200.0,
            obstructed=False,
        ),
        StillCandidate(
            at_seconds=30.0,
            quality_score=70.0,
            face_count=1,
            smile_count=1,
            sharpness=180.0,
            obstructed=False,
        ),
    ]


@pytest.mark.django_db
def test_handle_still_extraction_creates_stills_and_inherits_tags(storage_root, user):
    video = _create_type_a_video(storage_root=storage_root, user=user)
    tag = Tag.objects.create(slug="warmup", label="Warmup")
    video.tags.add(tag)
    params = ScoringParams.objects.get()
    job = Job.objects.create(
        video=video,
        job_type=Job.JobType.STILL_EXTRACTION,
        status=Job.Status.PROCESSING,
        scoring_params=params,
    )

    with (
        patch("apps.pipeline.handlers.gather_still_candidates", return_value=_candidates()),
        patch("apps.pipeline.handlers.run_ffmpeg_thumbnail") as run_thumbnail,
    ):
        handle_still_extraction(job)

    stills = list(Still.objects.filter(video=video).order_by("capture_seconds"))
    assert len(stills) == 2
    assert run_thumbnail.call_count == 2
    for index, (still, candidate) in enumerate(zip(stills, _candidates()), start=1):
        assert float(still.capture_seconds) == candidate.at_seconds
        assert still.quality_score == int(round(candidate.quality_score))
        assert still.face_count == candidate.face_count
        assert still.smile_count == candidate.smile_count
        assert still.sharpness == candidate.sharpness
        assert still.obstructed == candidate.obstructed
        assert still.scoring_params_id == params.pk
        assert still.storage_path.startswith("/nakavid/highlights/")
        assert f"__still_{index:03d}__p{params.pk}.jpg" in still.storage_path
        assert list(still.tags.values_list("slug", flat=True)) == ["warmup"]


@pytest.mark.django_db
def test_handle_still_extraction_rerun_only_replaces_its_own_params(storage_root, user):
    video = _create_type_a_video(storage_root=storage_root, user=user)
    params_a = ScoringParams.objects.get()
    params_b = ScoringParams.objects.create(still_count=params_a.still_count)

    with (
        patch("apps.pipeline.handlers.gather_still_candidates", return_value=_candidates()),
        patch("apps.pipeline.handlers.run_ffmpeg_thumbnail"),
    ):
        handle_still_extraction(
            Job.objects.create(
                video=video,
                job_type=Job.JobType.STILL_EXTRACTION,
                status=Job.Status.PROCESSING,
                scoring_params=params_a,
            )
        )
        handle_still_extraction(
            Job.objects.create(
                video=video,
                job_type=Job.JobType.STILL_EXTRACTION,
                status=Job.Status.PROCESSING,
                scoring_params=params_b,
            )
        )

    assert Still.objects.filter(video=video, scoring_params=params_a).count() == 2
    assert Still.objects.filter(video=video, scoring_params=params_b).count() == 2

    # Re-running params_a again only rebuilds its own two, not params_b's.
    with (
        patch("apps.pipeline.handlers.gather_still_candidates", return_value=_candidates()[:1]),
        patch("apps.pipeline.handlers.run_ffmpeg_thumbnail"),
    ):
        handle_still_extraction(
            Job.objects.create(
                video=video,
                job_type=Job.JobType.STILL_EXTRACTION,
                status=Job.Status.PROCESSING,
                scoring_params=params_a,
            )
        )

    assert Still.objects.filter(video=video, scoring_params=params_a).count() == 1
    assert Still.objects.filter(video=video, scoring_params=params_b).count() == 2


@pytest.mark.django_db
def test_handle_still_extraction_runs_for_type_b(storage_root, user):
    """A short recording is a single file end-to-end, same as a long one's."""
    video = _create_video(
        storage_root=storage_root,
        user=user,
        filename="IMG_2856.MOV",
        video_type=Video.VideoType.TYPE_B,
        orientation=Video.Orientation.MIXED,
        duration_seconds=20,
    )
    params = ScoringParams.objects.get()
    job = Job.objects.create(
        video=video,
        job_type=Job.JobType.STILL_EXTRACTION,
        status=Job.Status.PROCESSING,
        scoring_params=params,
    )

    with (
        patch("apps.pipeline.handlers.gather_still_candidates", return_value=_candidates()),
        patch("apps.pipeline.handlers.run_ffmpeg_thumbnail") as run_thumbnail,
    ):
        handle_still_extraction(job)

    stills = list(Still.objects.filter(video=video).order_by("capture_seconds"))
    assert len(stills) == 2
    assert run_thumbnail.call_count == 2
    for index, (still, candidate) in enumerate(zip(stills, _candidates()), start=1):
        assert float(still.capture_seconds) == candidate.at_seconds
        assert still.scoring_params_id == params.pk
        assert still.storage_path.startswith("/nakavid/highlights/")
        assert f"__still_{index:03d}__p{params.pk}.jpg" in still.storage_path
