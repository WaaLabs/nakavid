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
    params.target_clip_length_seconds = 6

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
    video.energy_curve = [
        {"start": 8.0, "end": 12.0, "score": 90.0, "signals": {"motion_energy": 0.2}},
        {"start": 28.0, "end": 32.0, "score": 80.0, "signals": {"motion_energy": 0.2}},
    ]
    video.save(update_fields=["energy_curve"])
    stale = Clip.objects.create(
        video=video,
        storage_path="/nakavid/highlights/2026/08/sample/stale__clip_009.mp4",
        start_seconds=Decimal("0.000"),
        end_seconds=Decimal("4.000"),
        created_by=user,
    )
    params = ScoringParams.objects.get()
    # Peaks here are ~20s apart, so keep clips short enough to stay distinct.
    params.target_clip_length_seconds = 6
    params.min_gap_seconds = 2
    params.save(update_fields=["target_clip_length_seconds", "min_gap_seconds"])
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
    # A previous run's clips are rebuilt, not left behind.
    assert not Clip.objects.filter(pk=stale.pk).exists()
    assert run_trim.call_count == 2
    assert run_thumbnail.call_count == 2
    for index, clip in enumerate(clips, start=1):
        assert clip.storage_path.startswith("/nakavid/highlights/")
        assert clip.storage_path.endswith(f"__clip_{index:03d}.mp4")
        assert clip.thumbnail_path.endswith(f"__clip_{index:03d}.jpg")
        assert clip.energy_curve
        assert list(clip.tags.values_list("slug", flat=True)) == ["warmup"]


def _give_video_a_curve(*, video: Video, user=None) -> None:
    video.energy_curve = [
        {"start": 8.0, "end": 12.0, "score": 90.0, "signals": {"motion_energy": 0.2}},
    ]
    video.highlight_score = 90
    video.save(update_fields=["energy_curve", "highlight_score"])


def _run_extraction(*, video: Video):
    job = Job.objects.create(
        video=video,
        job_type=Job.JobType.CLIP_EXTRACTION,
        status=Job.Status.PROCESSING,
        scoring_params=ScoringParams.objects.get(),
    )
    with (
        patch("apps.pipeline.handlers.run_ffmpeg_trim") as run_trim,
        patch("apps.pipeline.handlers.run_ffmpeg_thumbnail"),
    ):
        handle_clip_extraction(job)
    return run_trim


@pytest.mark.django_db
def test_extraction_cuts_from_the_transcoded_playback_file(storage_root, user):
    """Clips must come from the browser-safe copy, not a 10-bit original."""
    video = _create_type_a_video(storage_root=storage_root, user=user)
    playback_relative = build_originals_relative_path(
        recorded_at=timezone.now().date(),
        class_name="Kids A",
        theme="Summer Camp",
        filename="lesson__web.mp4",
    )
    video.playback_path = to_absolute_storage_path(storage_root, playback_relative)
    video.save(update_fields=["playback_path"])
    _give_video_a_curve(video=video)

    run_trim = _run_extraction(video=video)

    assert run_trim.call_count >= 1
    used = {str(call.kwargs["source_path"]) for call in run_trim.call_args_list}
    assert used == {str(storage_root / playback_relative)}


@pytest.mark.django_db
def test_extraction_falls_back_to_source_when_no_playback_file(storage_root, user):
    video = _create_type_a_video(storage_root=storage_root, user=user)
    assert video.playback_path == ""
    _give_video_a_curve(video=video)

    run_trim = _run_extraction(video=video)

    assert run_trim.call_count >= 1
    for call in run_trim.call_args_list:
        assert str(call.kwargs["source_path"]).endswith("lesson.mp4")


@pytest.mark.django_db
def test_target_clip_length_drives_the_clip_length():
    """Clip length must follow the parameter, not a hard-coded constant.

    It was pinned at ~6s by CLIP_EXPAND_SECONDS = 3.0, so every setting of
    these params produced the same short clips.
    """
    energy_curve = [
        {"start": 100.0, "end": 104.0, "score": 90.0, "signals": {"motion_energy": 0.2}},
    ]
    params = ScoringParams.objects.get()
    params.peak_count = 1
    params.min_clip_length_seconds = 4

    lengths = {}
    for target in (10, 30, 60):
        params.target_clip_length_seconds = target
        clips = select_clip_segments(
            energy_curve=energy_curve, params=params, duration_seconds=600.0
        )
        assert len(clips) == 1
        lengths[target] = clips[0].end_seconds - clips[0].start_seconds

    assert lengths[10] == pytest.approx(10.0, abs=2.0)
    assert lengths[30] == pytest.approx(30.0, abs=2.0)
    assert lengths[60] == pytest.approx(60.0, abs=2.0)


@pytest.mark.django_db
def test_min_gap_keeps_clips_from_running_together():
    """A busy stretch should yield one clip, not several near-adjacent ones."""
    energy_curve = [
        {"start": float(t), "end": float(t + 4), "score": 90.0 - i, "signals": {}}
        for i, t in enumerate(range(100, 160, 10))
    ]
    params = ScoringParams.objects.get()
    params.peak_count = 8
    params.target_clip_length_seconds = 10
    params.min_clip_length_seconds = 4

    params.min_gap_seconds = 1
    loose = select_clip_segments(energy_curve=energy_curve, params=params, duration_seconds=600.0)

    params.min_gap_seconds = 30
    tight = select_clip_segments(energy_curve=energy_curve, params=params, duration_seconds=600.0)

    assert len(tight) < len(loose)
    for earlier, later in zip(tight, tight[1:]):
        assert later.start_seconds - earlier.end_seconds >= 30


@pytest.mark.django_db
def test_extraction_without_a_curve_errors_rather_than_silently_doing_nothing(storage_root, user):
    """A missing curve must surface, not report done having rebuilt nothing.

    Extraction used to return early when it could not find the scoring row,
    so the job recorded done in milliseconds and the clips were never rebuilt.
    """
    from apps.pipeline.extraction import ClipExtractionError

    video = _create_type_a_video(storage_root=storage_root, user=user)
    assert video.energy_curve == []
    job = Job.objects.create(
        video=video,
        job_type=Job.JobType.CLIP_EXTRACTION,
        status=Job.Status.PROCESSING,
        scoring_params=ScoringParams.objects.get(),
    )

    with pytest.raises(ClipExtractionError, match="no energy curve"):
        handle_clip_extraction(job)
