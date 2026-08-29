"""Short recordings get the same treatment as long ones: playable and scored."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.library.models import Clip, Video
from apps.pipeline.handlers import handle_probe, handle_score
from apps.pipeline.models import Job, ScoringParams
from apps.pipeline.probe import ProbeResult
from apps.pipeline.scoring import SegmentScoringResult

User = get_user_model()


@pytest.fixture
def storage_root(tmp_path, settings):
    settings.NAKAVID_STORAGE_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def short_recording(db, storage_root):
    user = User.objects.create_user(username="phone", password="secret123!")
    video = Video.objects.create(
        title="IMG_2856",
        source_path="/nakavid/originals/2026/07/20260701_a_b/IMG_2856.MOV",
        video_type=Video.VideoType.TYPE_B,
        orientation=Video.Orientation.MIXED,
        class_name="A",
        theme="B",
        recorded_at=timezone.now(),
        duration_seconds=1,
        is_private=True,
        created_by=user,
    )
    Clip.objects.create(
        video=video,
        storage_path=video.source_path,
        start_seconds=Decimal("0.000"),
        end_seconds=Decimal("1.000"),
        created_by=user,
    )
    return video


@pytest.mark.django_db
def test_a_short_recording_that_is_not_browser_safe_gets_transcoded(short_recording):
    """The bug: only long recordings were checked, so phone clips stayed HEVC."""
    job = Job.objects.create(
        video=short_recording, job_type=Job.JobType.PROBE, status=Job.Status.PROCESSING
    )
    probe = ProbeResult(
        duration_seconds=12,
        orientation=Video.Orientation.PORTRAIT,
        video_codec="hevc",
        width=1080,
        height=1920,
        pixel_format="yuv420p10le",
        rotation=270,
    )

    with patch("apps.pipeline.handlers.run_ffprobe", return_value=probe):
        handle_probe(job)

    assert Job.objects.filter(video=short_recording, job_type=Job.JobType.TRANSCODE).exists()


@pytest.mark.django_db
def test_a_browser_safe_short_recording_goes_straight_to_scoring(short_recording):
    job = Job.objects.create(
        video=short_recording, job_type=Job.JobType.PROBE, status=Job.Status.PROCESSING
    )
    probe = ProbeResult(
        duration_seconds=12,
        orientation=Video.Orientation.PORTRAIT,
        video_codec="h264",
        width=1080,
        height=1920,
        pixel_format="yuv420p",
    )

    with patch("apps.pipeline.handlers.run_ffprobe", return_value=probe):
        handle_probe(job)

    assert Job.objects.filter(video=short_recording, job_type=Job.JobType.SCORE).exists()
    # A contact sheet only serves tuning of extraction, which does not apply.
    assert not Job.objects.filter(
        video=short_recording, job_type=Job.JobType.CONTACT_SHEET
    ).exists()


@pytest.mark.django_db
def test_scoring_a_short_recording_scores_its_clip_and_queues_no_extraction(
    short_recording, storage_root
):
    """It is already a clip, so there is nothing to cut — but it needs a score."""
    short_recording.duration_seconds = 20
    short_recording.save(update_fields=["duration_seconds"])
    job = Job.objects.create(
        video=short_recording,
        job_type=Job.JobType.SCORE,
        status=Job.Status.PROCESSING,
        scoring_params=ScoringParams.objects.get(),
    )
    curve = [{"start": 0.0, "end": 4.0, "score": 62.0, "signals": {}}]

    with (
        patch(
            "apps.pipeline.handlers.run_segment_scoring",
            return_value=SegmentScoringResult(energy_curve=curve, highlight_score=62),
        ),
        patch("apps.pipeline.handlers.run_ffmpeg_thumbnail") as thumbnail,
    ):
        handle_score(job)

    short_recording.refresh_from_db()
    clip = short_recording.clips.get()
    assert short_recording.highlight_score == 62
    assert clip.highlight_score == 62
    assert clip.energy_curve == curve
    assert thumbnail.call_count == 1
    assert clip.thumbnail_path.endswith("IMG_2856__thumb.jpg")
    assert not Job.objects.filter(
        video=short_recording, job_type=Job.JobType.CLIP_EXTRACTION
    ).exists()


@pytest.mark.django_db
def test_clips_browser_filters_by_source(client, short_recording, storage_root):
    """Extracted clips and uploaded short recordings can be told apart."""
    user = short_recording.created_by
    long_recording = Video.objects.create(
        title="lesson",
        source_path="/nakavid/originals/2026/07/20260701_a_b/lesson.mp4",
        video_type=Video.VideoType.TYPE_A,
        orientation=Video.Orientation.LANDSCAPE,
        class_name="A",
        theme="B",
        recorded_at=timezone.now(),
        duration_seconds=600,
        is_private=True,
        created_by=user,
    )
    extracted = Clip.objects.create(
        video=long_recording,
        storage_path="/nakavid/highlights/2026/07/20260701_a_b/lesson__clip_001.mp4",
        start_seconds=Decimal("10.000"),
        end_seconds=Decimal("40.000"),
        highlight_score=70,
        created_by=user,
    )
    uploaded = short_recording.clips.get()

    assert client.login(username="phone", password="secret123!")
    url = reverse("clips-browser")

    everything = client.get(url).context["clips"]
    assert {clip.pk for clip in everything} == {extracted.pk, uploaded.pk}

    only_extracted = client.get(url, {"source": "extracted"}).context["clips"]
    assert [clip.pk for clip in only_extracted] == [extracted.pk]

    only_uploaded = client.get(url, {"source": "uploaded"}).context["clips"]
    assert [clip.pk for clip in only_uploaded] == [uploaded.pk]


@pytest.mark.django_db
def test_transcoding_points_the_clip_at_the_playable_rendition(short_recording, storage_root):
    """Otherwise the rendition exists but nothing serves it.

    A short recording's clip pointed at the original file, so a transcoded
    HEVC upload still streamed the HEVC — the browser-safe copy was never used.
    """
    from apps.pipeline.handlers import handle_transcode

    original = short_recording.source_path
    job = Job.objects.create(
        video=short_recording,
        job_type=Job.JobType.TRANSCODE,
        status=Job.Status.PROCESSING,
    )

    with patch("apps.pipeline.handlers.run_ffmpeg_web_transcode"):
        handle_transcode(job)

    short_recording.refresh_from_db()
    clip = short_recording.clips.get()
    assert short_recording.playback_path.endswith("__web.mp4")
    assert clip.storage_path == short_recording.playback_path
    assert clip.storage_path != original
    # The original is still recorded on the video.
    assert short_recording.source_path == original
