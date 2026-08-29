"""The tuning page re-ranks clips without re-scoring."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.library.models import Video
from apps.pipeline.models import Job, ScoringParams
from apps.pipeline.scoring import rescore_energy_curve

User = get_user_model()


def _curve() -> list[dict]:
    """A quiet stretch, a face-heavy peak, then a loud peak."""
    points = []
    for index in range(60):
        seconds = index * 2.0
        if 20 <= index < 26:
            signals = {
                "face_count": 8.0,
                "smile_ratio": 2.0,
                "motion_energy": 0.1,
                "audio_rms": 0.01,
            }
        elif 40 <= index < 46:
            signals = {
                "face_count": 0.5,
                "smile_ratio": 0.0,
                "motion_energy": 0.6,
                "audio_rms": 0.9,
            }
        else:
            signals = {
                "face_count": 0.5,
                "smile_ratio": 0.0,
                "motion_energy": 0.05,
                "audio_rms": 0.02,
            }
        points.append({"start": seconds, "end": seconds + 4.0, "score": 0.0, "signals": signals})
    return points


@pytest.fixture
def video(db):
    user = User.objects.create_user(username="tuner", password="secret123!")
    return Video.objects.create(
        title="Tuning Sample",
        source_path="/nakavid/originals/2026/08/20260826_a_b/lesson.mp4",
        video_type=Video.VideoType.TYPE_A,
        orientation=Video.Orientation.LANDSCAPE,
        class_name="A",
        theme="B",
        recorded_at=timezone.now(),
        duration_seconds=130,
        width=1920,
        height=1080,
        is_private=True,
        created_by=user,
        energy_curve=_curve(),
    )


@pytest.fixture
def client_logged_in(client, video):
    assert client.login(username="tuner", password="secret123!")
    return client


@pytest.mark.django_db
def test_tuning_page_requires_login(client, video):
    response = client.get(reverse("scoring-tuning"))

    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")


@pytest.mark.django_db
def test_weights_change_which_moments_win(client_logged_in, video):
    """The whole point: re-weighting re-ranks without touching the signals."""
    url = reverse("scoring-tuning")
    base = {
        "video": str(video.pk),
        "silence_penalty_weight": "0.1",
        "silence_rms_threshold": "0.01",
        "smoothing_window_count": "3",
        "target_clip_length_seconds": "10",
        "min_clip_length_seconds": "4",
        "min_gap_seconds": "10",
        "peak_count": "1",
    }

    faces = client_logged_in.get(
        url,
        {
            **base,
            "face_weight": "1",
            "smile_weight": "0",
            "motion_weight": "0",
            "audio_weight": "0",
        },
    )
    audio = client_logged_in.get(
        url,
        {
            **base,
            "face_weight": "0",
            "smile_weight": "0",
            "motion_weight": "0",
            "audio_weight": "1",
        },
    )

    assert faces.status_code == 200 and audio.status_code == 200
    face_pick = faces.context["candidates"][0]
    audio_pick = audio.context["candidates"][0]

    # The face-heavy stretch sits around 40-52s, the loud one around 80-92s.
    assert 30 <= face_pick["start_seconds"] <= 60
    assert 70 <= audio_pick["start_seconds"] <= 100
    assert face_pick["start_seconds"] != audio_pick["start_seconds"]


@pytest.mark.django_db
def test_preview_does_not_touch_stored_state(client_logged_in, video):
    before_params = ScoringParams.objects.count()
    before_curve = list(video.energy_curve)

    client_logged_in.get(
        reverse("scoring-tuning"),
        {
            "video": str(video.pk),
            "face_weight": "0.9",
            "smile_weight": "0",
            "motion_weight": "0",
            "audio_weight": "0.1",
            "silence_penalty_weight": "0.1",
            "silence_rms_threshold": "0.01",
            "smoothing_window_count": "3",
            "target_clip_length_seconds": "20",
            "min_clip_length_seconds": "4",
            "min_gap_seconds": "10",
            "peak_count": "3",
        },
    )

    video.refresh_from_db()
    assert ScoringParams.objects.count() == before_params
    assert video.energy_curve == before_curve


@pytest.mark.django_db
def test_flat_signals_are_called_out(client_logged_in, video):
    response = client_logged_in.get(reverse("scoring-tuning"))

    summary = {row["name"]: row for row in response.context["summary"]}
    # smile_ratio varies in the fixture; a constant signal would be flagged.
    assert summary["smile ratio"]["is_flat"] is False
    video.energy_curve = [
        {**point, "signals": {**point["signals"], "smile_ratio": 0.0}}
        for point in video.energy_curve
    ]
    video.save(update_fields=["energy_curve"])

    response = client_logged_in.get(reverse("scoring-tuning"))
    summary = {row["name"]: row for row in response.context["summary"]}
    assert summary["smile ratio"]["is_flat"] is True


@pytest.mark.django_db
def test_apply_saves_params_and_queues_extraction_not_scoring(client_logged_in, video):
    response = client_logged_in.post(
        reverse("scoring-tuning-apply"),
        {
            "video_id": str(video.pk),
            "face_weight": "0.5",
            "smile_weight": "0",
            "motion_weight": "0.5",
            "audio_weight": "0",
            "silence_penalty_weight": "0.1",
            "silence_rms_threshold": "0.01",
            "smoothing_window_count": "3",
            "target_clip_length_seconds": "30",
            "min_clip_length_seconds": "4",
            "min_gap_seconds": "15",
            "peak_count": "8",
        },
    )

    assert response.status_code == 302
    params = ScoringParams.objects.order_by("-pk").first()
    assert float(params.face_weight) == 0.5
    assert params.target_clip_length_seconds == 30

    jobs = Job.objects.filter(video=video)
    assert jobs.count() == 1
    assert jobs.first().job_type == Job.JobType.CLIP_EXTRACTION
    assert not jobs.filter(job_type=Job.JobType.SCORE).exists()

    video.refresh_from_db()
    assert any(point["score"] > 0 for point in video.energy_curve)


@pytest.mark.django_db
def test_rescore_preserves_signals_and_only_moves_scores(video):
    params = ScoringParams.objects.get()
    params.face_weight = 1
    params.smile_weight = 0
    params.motion_weight = 0
    params.audio_weight = 0

    rescored = rescore_energy_curve(energy_curve=video.energy_curve, params=params)

    assert len(rescored) == len(video.energy_curve)
    for before, after in zip(video.energy_curve, rescored):
        assert after["signals"] == before["signals"]
        assert after["start"] == before["start"]
    assert any(point["score"] > 0 for point in rescored)


@pytest.mark.django_db
def test_tuning_defaults_to_a_recording_when_none_is_named(client_logged_in, video):
    """It is a settings page, so it must work without a recording in the URL."""
    response = client_logged_in.get(reverse("scoring-tuning"))

    assert response.status_code == 200
    assert response.context["video"].pk == video.pk
    assert list(response.context["recordings"]) == [video]


@pytest.mark.django_db
def test_unscored_recordings_are_not_offered_for_preview(client_logged_in, video):
    """Previewing needs a stored curve, so a bare recording is not a choice."""
    unscored = Video.objects.create(
        title="Never Scored",
        source_path="/nakavid/originals/2026/08/20260826_a_b/other.mp4",
        video_type=Video.VideoType.TYPE_A,
        orientation=Video.Orientation.LANDSCAPE,
        class_name="A",
        theme="B",
        recorded_at=timezone.now(),
        duration_seconds=100,
        is_private=True,
        created_by=video.created_by,
    )

    response = client_logged_in.get(reverse("scoring-tuning"))

    assert unscored not in response.context["recordings"]
    assert video in response.context["recordings"]


@pytest.mark.django_db
def test_tuning_page_survives_having_nothing_to_preview(client, db):
    """A fresh install has no scored recordings; the page must still render."""
    User.objects.create_user(username="fresh", password="secret123!")
    assert client.login(username="fresh", password="secret123!")

    response = client.get(reverse("scoring-tuning"))

    assert response.status_code == 200
    assert response.context["video"] is None
    assert response.context["recordings"] == []


@pytest.mark.django_db
def test_apply_says_the_settings_are_global(client_logged_in, video):
    """The params are global; only the previewed recording is rebuilt."""
    response = client_logged_in.post(
        reverse("scoring-tuning-apply"),
        {
            "video_id": str(video.pk),
            "face_weight": "0.5",
            "smile_weight": "0",
            "motion_weight": "0.5",
            "audio_weight": "0",
            "silence_penalty_weight": "0.1",
            "silence_rms_threshold": "0.01",
            "smoothing_window_count": "3",
            "target_clip_length_seconds": "30",
            "min_clip_length_seconds": "4",
            "min_gap_seconds": "15",
            "peak_count": "8",
        },
        follow=True,
    )

    assert response.status_code == 200
    message = " ".join(str(m) for m in response.context["messages"])
    assert "all future scoring" in message
    assert video.title in message
