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


def _contiguous_curve(
    *, scores: dict[int, float], step_seconds: int = 2, count: int = 20
) -> list[dict]:
    """A run of contiguous, non-overlapping windows — a synthetic energy curve
    with no gaps, so plateau growth has real neighbours to walk across.
    scores maps a window's start second to its score; anything unlisted is 10."""
    return [
        {
            "start": float(t),
            "end": float(t + step_seconds),
            "score": scores.get(t, 10.0),
            "signals": {"motion_energy": 0.5},
        }
        for t in range(0, count * step_seconds, step_seconds)
    ]


@pytest.mark.django_db
def test_variable_mode_gives_a_short_spike_a_short_clip():
    """A lone high window surrounded by low ones should not be padded out to
    the same length as a long good stretch — that's the whole point of
    plateau growth over a fixed expand."""
    energy_curve = _contiguous_curve(scores={20: 90.0})
    params = ScoringParams.objects.get()
    params.clip_length_mode = ScoringParams.ClipLengthMode.VARIABLE
    params.peak_count = 1
    params.min_clip_length_seconds = 4
    params.max_clip_length_seconds = 60
    params.plateau_score_ratio = Decimal("0.60")

    clips = select_clip_segments(energy_curve=energy_curve, params=params, duration_seconds=40.0)

    assert len(clips) == 1
    length = clips[0].end_seconds - clips[0].start_seconds
    # Neighbours score 10, well under 90 * 0.6 = 54, so growth stops at the
    # peak window itself and only the min-length floor pads it out.
    assert length == pytest.approx(4.0, abs=1.0)


@pytest.mark.django_db
def test_variable_mode_gives_a_long_plateau_a_long_clip():
    """A wide run of near-peak scores should grow the clip across all of it,
    not stop at whatever the fixed target length would have been."""
    plateau_scores = {t: 80.0 for t in range(10, 32, 2)} | {20: 90.0}
    energy_curve = _contiguous_curve(scores=plateau_scores)
    params = ScoringParams.objects.get()
    params.clip_length_mode = ScoringParams.ClipLengthMode.VARIABLE
    params.peak_count = 1
    params.min_clip_length_seconds = 4
    params.max_clip_length_seconds = 60
    params.plateau_score_ratio = Decimal("0.60")

    clips = select_clip_segments(energy_curve=energy_curve, params=params, duration_seconds=40.0)

    assert len(clips) == 1
    length = clips[0].end_seconds - clips[0].start_seconds
    # Plateau runs from t=10 to t=32 (score >= 90*0.6=54 throughout), so the
    # clip should span roughly that whole run rather than a fixed width.
    assert length > 15.0


@pytest.mark.django_db
def test_variable_mode_clamps_to_max_clip_length():
    plateau_scores = {t: 80.0 for t in range(0, 40, 2)} | {20: 90.0}
    energy_curve = _contiguous_curve(scores=plateau_scores)
    params = ScoringParams.objects.get()
    params.clip_length_mode = ScoringParams.ClipLengthMode.VARIABLE
    params.peak_count = 1
    params.min_clip_length_seconds = 4
    params.max_clip_length_seconds = 10
    params.plateau_score_ratio = Decimal("0.60")

    clips = select_clip_segments(energy_curve=energy_curve, params=params, duration_seconds=80.0)

    assert len(clips) == 1
    length = clips[0].end_seconds - clips[0].start_seconds
    assert length <= 10.0 + 1e-6


@pytest.mark.django_db
def test_fixed_and_variable_extraction_coexist_on_the_same_video(storage_root, user):
    """The whole point of the eval flow: two ScoringParams rows produce two
    independent sets of clips on the same video, and neither run's clips
    wipe the other's."""
    video = _create_type_a_video(storage_root=storage_root, user=user)
    video.energy_curve = _contiguous_curve(scores={20: 90.0})
    video.save(update_fields=["energy_curve"])

    fixed_params = ScoringParams.objects.get()
    fixed_params.target_clip_length_seconds = 6
    fixed_params.min_clip_length_seconds = 4
    fixed_params.peak_count = 1
    fixed_params.save()

    variable_params = ScoringParams.objects.create(
        clip_length_mode=ScoringParams.ClipLengthMode.VARIABLE,
        target_clip_length_seconds=6,
        min_clip_length_seconds=4,
        max_clip_length_seconds=60,
        plateau_score_ratio=Decimal("0.60"),
        peak_count=1,
    )

    with (
        patch("apps.pipeline.handlers.run_ffmpeg_trim"),
        patch("apps.pipeline.handlers.run_ffmpeg_thumbnail"),
    ):
        handle_clip_extraction(
            Job.objects.create(
                video=video,
                job_type=Job.JobType.CLIP_EXTRACTION,
                status=Job.Status.PROCESSING,
                scoring_params=fixed_params,
            )
        )
        handle_clip_extraction(
            Job.objects.create(
                video=video,
                job_type=Job.JobType.CLIP_EXTRACTION,
                status=Job.Status.PROCESSING,
                scoring_params=variable_params,
            )
        )

    fixed_clips = list(Clip.objects.filter(video=video, scoring_params=fixed_params))
    variable_clips = list(Clip.objects.filter(video=video, scoring_params=variable_params))
    assert len(fixed_clips) == 1
    assert len(variable_clips) == 1
    # Different storage paths — the variant suffix is what stops the second
    # run's ffmpeg output from overwriting the first's on disk.
    assert fixed_clips[0].storage_path != variable_clips[0].storage_path
    assert list(variable_clips[0].tags.values_list("slug", flat=True)) == ["eval-variable-length"]
    assert list(fixed_clips[0].tags.values_list("slug", flat=True)) == []

    # Re-running the fixed job replaces only its own clip, not the eval one.
    with (
        patch("apps.pipeline.handlers.run_ffmpeg_trim"),
        patch("apps.pipeline.handlers.run_ffmpeg_thumbnail"),
    ):
        handle_clip_extraction(
            Job.objects.create(
                video=video,
                job_type=Job.JobType.CLIP_EXTRACTION,
                status=Job.Status.PROCESSING,
                scoring_params=fixed_params,
            )
        )

    assert Clip.objects.filter(video=video, scoring_params=variable_params).count() == 1
    assert Clip.objects.filter(video=video, scoring_params=fixed_params).count() == 1


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
