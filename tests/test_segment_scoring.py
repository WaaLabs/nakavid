from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.library.models import Clip, Video
from apps.library.storage_paths import build_originals_relative_path, to_absolute_storage_path
from apps.pipeline.handlers import handle_probe, handle_score
from apps.pipeline.models import Job, ScoringParams
from apps.pipeline.probe import ProbeResult
from apps.pipeline.scoring import (
    SegmentScoringResult,
    WindowSignals,
    aggregate_window_score,
    build_windows,
    score_windows,
    smooth_scores,
)
from apps.pipeline.worker import process_job

User = get_user_model()


def test_build_windows_full_coverage():
    windows = build_windows(duration_seconds=10.0, window_size_seconds=4.0, step_seconds=2.0)

    assert windows == [(0.0, 4.0), (2.0, 6.0), (4.0, 8.0), (6.0, 10.0)]


def test_build_windows_short_video():
    windows = build_windows(duration_seconds=2.5, window_size_seconds=4.0, step_seconds=2.0)

    assert windows == [(0.0, 2.5)]


def test_build_windows_empty_duration():
    assert build_windows(duration_seconds=0.0, window_size_seconds=4.0, step_seconds=2.0) == []


def test_aggregate_window_score_uses_scoring_params():
    params = ScoringParams(
        face_weight=Decimal("0.500"),
        smile_weight=Decimal("0.000"),
        motion_weight=Decimal("0.250"),
        audio_weight=Decimal("0.250"),
        silence_penalty_weight=Decimal("0.100"),
        silence_rms_threshold=Decimal("0.0100"),
    )
    signals = WindowSignals(
        face_count=3.0,
        smile_ratio=0.0,
        motion_energy=0.5,
        audio_rms=0.05,
    )

    score = aggregate_window_score(signals=signals, params=params)

    assert score == pytest.approx(75.0)


def test_aggregate_window_score_applies_silence_penalty():
    params = ScoringParams(
        face_weight=Decimal("0.250"),
        smile_weight=Decimal("0.250"),
        motion_weight=Decimal("0.250"),
        audio_weight=Decimal("0.250"),
        silence_penalty_weight=Decimal("0.200"),
        silence_rms_threshold=Decimal("0.0500"),
    )
    loud = WindowSignals(face_count=1.0, smile_ratio=0.5, motion_energy=0.5, audio_rms=0.08)
    silent = WindowSignals(face_count=1.0, smile_ratio=0.5, motion_energy=0.5, audio_rms=0.01)

    loud_score = aggregate_window_score(signals=loud, params=params)
    silent_score = aggregate_window_score(signals=silent, params=params)

    assert silent_score < loud_score
    assert loud_score - silent_score == pytest.approx(37.5)


def test_smooth_scores_moving_average():
    assert smooth_scores([10.0, 20.0, 30.0, 40.0], window_size=3) == pytest.approx(
        [15.0, 20.0, 30.0, 35.0]
    )


def test_score_windows_energy_curve_shape(db):
    params = ScoringParams.objects.get()

    def signal_loader(start_seconds: float, end_seconds: float) -> WindowSignals:
        return WindowSignals(
            face_count=1.0,
            smile_ratio=0.2,
            motion_energy=0.3,
            audio_rms=0.04,
        )

    result = score_windows(duration_seconds=6.0, params=params, signal_loader=signal_loader)

    assert len(result.energy_curve) == len(
        build_windows(
            duration_seconds=6.0,
            window_size_seconds=float(params.window_size_seconds),
            step_seconds=float(params.step_seconds),
        )
    )
    for point in result.energy_curve:
        assert {"start", "end", "score", "signals"} <= set(point.keys())
        assert point["start"] < point["end"]
        assert 0 <= point["score"] <= 100
    assert 0 <= result.highlight_score <= 100


@pytest.fixture
def storage_root(tmp_path, settings):
    settings.NAKAVID_STORAGE_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def user(db):
    return User.objects.create_user(username="score-test", password="secret123!")


def _create_type_a_video(*, storage_root: Path, user) -> Video:
    relative_path = build_originals_relative_path(
        recorded_at=timezone.now().date(),
        class_name="A",
        theme="Scoring",
        filename="lesson.mp4",
    )
    absolute_path = to_absolute_storage_path(storage_root, relative_path)
    file_path = storage_root / relative_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(b"fake-video")

    return Video.objects.create(
        title="Scoring Sample",
        source_path=absolute_path,
        video_type=Video.VideoType.TYPE_A,
        orientation=Video.Orientation.LANDSCAPE,
        class_name="A",
        theme="Scoring",
        recorded_at=timezone.now(),
        duration_seconds=12,
        is_private=True,
        created_by=user,
    )


@pytest.mark.django_db
def test_handle_probe_enqueues_score_job_for_type_a(storage_root, user):
    video = _create_type_a_video(storage_root=storage_root, user=user)
    job = Job.objects.create(video=video, job_type=Job.JobType.PROBE, status=Job.Status.PROCESSING)

    with patch(
        "apps.pipeline.handlers.run_ffprobe",
        return_value=ProbeResult(
            duration_seconds=120,
            orientation=Video.Orientation.LANDSCAPE,
            video_codec="h264",
            width=1920,
            height=1080,
        ),
    ):
        handle_probe(job)

    score_job = Job.objects.filter(video=video, job_type=Job.JobType.SCORE).first()
    assert score_job is not None
    assert score_job.status == Job.Status.PENDING
    assert score_job.scoring_params_id is not None


@pytest.mark.django_db
def test_handle_score_persists_energy_curve(storage_root, user):
    video = _create_type_a_video(storage_root=storage_root, user=user)
    params = ScoringParams.objects.get()
    job = Job.objects.create(
        video=video,
        job_type=Job.JobType.SCORE,
        status=Job.Status.PROCESSING,
        scoring_params=params,
    )
    scoring_result = SegmentScoringResult(
        energy_curve=[
            {"start": 0.0, "end": 4.0, "score": 72.5, "signals": {}},
            {"start": 2.0, "end": 6.0, "score": 80.0, "signals": {}},
        ],
        highlight_score=80,
    )

    with patch("apps.pipeline.handlers.run_segment_scoring", return_value=scoring_result):
        handle_score(job)

    clip = Clip.objects.get(video=video)
    assert clip.energy_curve == scoring_result.energy_curve
    assert clip.highlight_score == 80
    assert clip.start_seconds == Decimal("0.000")
    assert clip.end_seconds == Decimal("12")


@pytest.mark.django_db
def test_handle_score_updates_existing_clip(storage_root, user):
    video = _create_type_a_video(storage_root=storage_root, user=user)
    clip = Clip.objects.create(
        video=video,
        storage_path=video.source_path,
        start_seconds=Decimal("0.000"),
        end_seconds=Decimal("1"),
        created_by=user,
    )
    job = Job.objects.create(video=video, job_type=Job.JobType.SCORE, status=Job.Status.PROCESSING)
    scoring_result = SegmentScoringResult(
        energy_curve=[{"start": 0.0, "end": 4.0, "score": 55.0}],
        highlight_score=55,
    )

    with patch("apps.pipeline.handlers.run_segment_scoring", return_value=scoring_result):
        handle_score(job)

    clip.refresh_from_db()
    assert clip.energy_curve == scoring_result.energy_curve
    assert clip.highlight_score == 55


@pytest.mark.django_db
def test_process_job_score_failure_sets_error_stderr(storage_root, user):
    video = _create_type_a_video(storage_root=storage_root, user=user)
    job = Job.objects.create(
        video=video,
        job_type=Job.JobType.SCORE,
        status=Job.Status.PROCESSING,
        claimed_at=timezone.now(),
    )

    with patch(
        "apps.pipeline.handlers.run_segment_scoring",
        side_effect=__import__("apps.pipeline.scoring", fromlist=["ScoringError"]).ScoringError(
            "Unable to open video for scoring"
        ),
    ):
        process_job(job)

    job.refresh_from_db()
    assert job.status == Job.Status.ERROR
    assert job.stderr == "Unable to open video for scoring"
