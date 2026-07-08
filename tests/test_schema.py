import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.utils import timezone

from apps.library.models import Clip, Combine, CombineClip, Video
from apps.pipeline.models import Job, ScoringParams

User = get_user_model()


@pytest.mark.django_db
def test_video_and_clip_schema_constraints():
    user = User.objects.create_user(username="owner", password="secret123!")
    video = Video.objects.create(
        title="Morning Class",
        source_path="/nakavid/originals/2026/07/20260707_a_theme/source.mp4",
        video_type=Video.VideoType.TYPE_A,
        orientation=Video.Orientation.LANDSCAPE,
        class_name="A",
        theme="Animals",
        recorded_at=timezone.now(),
        duration_seconds=300,
        is_private=True,
        created_by=user,
    )

    Clip.objects.create(
        video=video,
        storage_path="/nakavid/highlights/2026/07/20260707_a_theme/clip_001.mp4",
        start_seconds="0.000",
        end_seconds="5.000",
        highlight_score=75,
        created_by=user,
    )

    with pytest.raises(IntegrityError):
        Clip.objects.create(
            video=video,
            storage_path="/nakavid/highlights/2026/07/20260707_a_theme/clip_002.mp4",
            start_seconds="5.000",
            end_seconds="2.000",
            highlight_score=80,
            created_by=user,
        )


@pytest.mark.django_db
def test_job_defaults_to_pending():
    user = User.objects.create_user(username="operator", password="secret123!")
    video = Video.objects.create(
        title="Type B Clip",
        source_path="/nakavid/originals/2026/07/20260707_b_theme/input.mp4",
        video_type=Video.VideoType.TYPE_B,
        orientation=Video.Orientation.MIXED,
        class_name="B",
        theme="Colors",
        recorded_at=timezone.now(),
        duration_seconds=30,
        is_private=True,
        created_by=user,
    )

    job = Job.objects.create(video=video, job_type=Job.JobType.CLIP_EXTRACTION)

    assert job.status == Job.Status.PENDING


@pytest.mark.django_db
def test_combine_preserves_clip_order():
    user = User.objects.create_user(username="editor", password="secret123!")
    video = Video.objects.create(
        title="Lesson",
        source_path="/nakavid/originals/2026/07/20260707_c_theme/source.mp4",
        video_type=Video.VideoType.TYPE_A,
        orientation=Video.Orientation.LANDSCAPE,
        class_name="C",
        theme="Food",
        recorded_at=timezone.now(),
        duration_seconds=120,
        is_private=True,
        created_by=user,
    )
    clip_a = Clip.objects.create(
        video=video,
        storage_path="/nakavid/highlights/2026/07/20260707_c_theme/clip_001.mp4",
        start_seconds="0.000",
        end_seconds="5.000",
        created_by=user,
    )
    clip_b = Clip.objects.create(
        video=video,
        storage_path="/nakavid/highlights/2026/07/20260707_c_theme/clip_002.mp4",
        start_seconds="5.000",
        end_seconds="10.000",
        created_by=user,
    )
    combine = Combine.objects.create(title="Week 1 Highlights", created_by=user)
    CombineClip.objects.create(combine=combine, clip=clip_b, position=2)
    CombineClip.objects.create(combine=combine, clip=clip_a, position=1)

    ordered = list(combine.combine_clips.values_list("clip_id", flat=True))

    assert ordered == [clip_a.id, clip_b.id]


@pytest.mark.django_db
def test_default_scoring_params_seeded():
    params = ScoringParams.objects.get()
    assert params.face_weight == pytest.approx(0.250)
    assert params.smile_weight == pytest.approx(0.250)
    assert params.motion_weight == pytest.approx(0.250)
    assert params.audio_weight == pytest.approx(0.250)
    assert float(params.silence_penalty_weight) == pytest.approx(0.100)
    assert float(params.silence_rms_threshold) == pytest.approx(0.0100)
    assert params.window_size_seconds == 4
    assert params.step_seconds == 2
    assert params.min_clip_length_seconds == 4
    assert params.min_gap_seconds == 2
    assert params.peak_count == 8
