"""The one-off trigger for running still extraction against a video."""

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
    return User.objects.create_user(username="still-cli-test", password="secret123!")


def _video(*, user) -> Video:
    return Video.objects.create(
        title="Still CLI Sample",
        source_path="/nakavid/originals/2026/09/lesson.mp4",
        video_type=Video.VideoType.TYPE_A,
        orientation=Video.Orientation.LANDSCAPE,
        recorded_at=timezone.now(),
        duration_seconds=120,
        is_private=True,
        created_by=user,
    )


@pytest.mark.django_db
def test_enqueues_a_still_extraction_job_against_the_given_params(user):
    video = _video(user=user)
    params = ScoringParams.objects.get()

    out = StringIO()
    call_command("enqueue_still_extraction", str(video.pk), str(params.pk), stdout=out)

    job = Job.objects.get(video=video, job_type=Job.JobType.STILL_EXTRACTION)
    assert job.scoring_params_id == params.pk
    assert job.status == Job.Status.PENDING
    assert str(job.pk) in out.getvalue()


@pytest.mark.django_db
def test_errors_on_unknown_video(user):
    params = ScoringParams.objects.get()
    with pytest.raises(CommandError, match="No video"):
        call_command("enqueue_still_extraction", "999999", str(params.pk))


@pytest.mark.django_db
def test_errors_on_unknown_scoring_params(user):
    video = _video(user=user)
    with pytest.raises(CommandError, match="No ScoringParams"):
        call_command("enqueue_still_extraction", str(video.pk), "999999")
