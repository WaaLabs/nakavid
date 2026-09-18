"""The one-off trigger for comparing extraction approaches on the same video."""

from __future__ import annotations

from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.utils import timezone

from apps.library.models import Video
from apps.pipeline.models import Job, ScoringParams

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(username="eval-cli-test", password="secret123!")


def _video(*, user, energy_curve: list[dict] | None = None) -> Video:
    return Video.objects.create(
        title="Eval CLI Sample",
        source_path="/nakavid/originals/2026/09/lesson.mp4",
        video_type=Video.VideoType.TYPE_A,
        orientation=Video.Orientation.LANDSCAPE,
        recorded_at=timezone.now(),
        duration_seconds=120,
        energy_curve=energy_curve or [],
        is_private=True,
        created_by=user,
    )


@pytest.mark.django_db
def test_enqueues_a_clip_extraction_job_against_the_given_params(user):
    video = _video(user=user, energy_curve=[{"start": 0.0, "end": 4.0, "score": 90.0}])
    params = ScoringParams.objects.create(clip_length_mode=ScoringParams.ClipLengthMode.VARIABLE)

    out = StringIO()
    call_command("enqueue_clip_extraction_eval", str(video.pk), str(params.pk), stdout=out)

    job = Job.objects.get(video=video, job_type=Job.JobType.CLIP_EXTRACTION)
    assert job.scoring_params_id == params.pk
    assert job.status == Job.Status.PENDING
    assert str(job.pk) in out.getvalue()


@pytest.mark.django_db
def test_errors_on_unknown_video(user):
    params = ScoringParams.objects.get()
    with pytest.raises(CommandError, match="No video"):
        call_command("enqueue_clip_extraction_eval", "999999", str(params.pk))


@pytest.mark.django_db
def test_errors_on_unknown_scoring_params(user):
    video = _video(user=user, energy_curve=[{"start": 0.0, "end": 4.0, "score": 90.0}])
    with pytest.raises(CommandError, match="No ScoringParams"):
        call_command("enqueue_clip_extraction_eval", str(video.pk), "999999")


@pytest.mark.django_db
def test_errors_when_video_has_no_energy_curve_yet(user):
    video = _video(user=user, energy_curve=[])
    params = ScoringParams.objects.get()
    with pytest.raises(CommandError, match="no energy curve"):
        call_command("enqueue_clip_extraction_eval", str(video.pk), str(params.pk))
