"""Ingesting an existing folder of footage, without the browser."""

from __future__ import annotations

import shutil
import subprocess
from io import StringIO
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command

from apps.library.models import Video
from apps.pipeline.models import Job

User = get_user_model()

ffmpeg_required = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available")


@pytest.fixture
def storage_root(tmp_path, settings):
    root = tmp_path / "library"
    root.mkdir()
    settings.NAKAVID_STORAGE_ROOT = root
    return root


@pytest.fixture
def superuser(db):
    return User.objects.create_superuser(username="founder", password="secret123!")


def _write_video(path: Path, *, seconds: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={seconds}:size=160x120:rate=10",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


@ffmpeg_required
@pytest.mark.django_db
def test_ingests_every_video_and_queues_a_probe(tmp_path, storage_root, superuser):
    incoming = tmp_path / "incoming"
    _write_video(incoming / "one.mp4")
    _write_video(incoming / "two.mov")
    (incoming / "notes.txt").write_text("not a video")

    call_command(
        "ingest_directory", incoming, class_name="Quokka", theme="Lesson", stdout=StringIO()
    )

    videos = Video.objects.order_by("title")
    assert [video.title for video in videos] == ["one", "two"]
    for video in videos:
        assert video.video_type == Video.VideoType.TYPE_A
        assert video.class_name == "Quokka"
        assert (storage_root / video.source_path.removeprefix("/nakavid/")).exists()
        assert Job.objects.filter(video=video, job_type=Job.JobType.PROBE).exists()


@ffmpeg_required
@pytest.mark.django_db
def test_is_idempotent(tmp_path, storage_root, superuser):
    """Re-running over the same folder must not duplicate the library."""
    incoming = tmp_path / "incoming"
    _write_video(incoming / "one.mp4")

    call_command("ingest_directory", incoming, class_name="A", theme="B", stdout=StringIO())
    second = StringIO()
    call_command("ingest_directory", incoming, class_name="A", theme="B", stdout=second)

    assert Video.objects.count() == 1
    assert "skipped 1 already present" in second.getvalue()


@ffmpeg_required
@pytest.mark.django_db
def test_dry_run_writes_nothing(tmp_path, storage_root, superuser):
    incoming = tmp_path / "incoming"
    _write_video(incoming / "one.mp4")
    out = StringIO()

    call_command("ingest_directory", incoming, class_name="A", theme="B", dry_run=True, stdout=out)

    assert Video.objects.count() == 0
    assert not any(storage_root.rglob("*.mp4"))
    assert "would ingest 1 file(s)" in out.getvalue()


@ffmpeg_required
@pytest.mark.django_db
def test_short_recordings_get_their_clip_row(tmp_path, storage_root, superuser):
    incoming = tmp_path / "incoming"
    _write_video(incoming / "phone.mp4")

    call_command(
        "ingest_directory", incoming, class_name="A", theme="B", type="short", stdout=StringIO()
    )

    video = Video.objects.get()
    assert video.video_type == Video.VideoType.TYPE_B
    assert video.clips.count() == 1


@ffmpeg_required
@pytest.mark.django_db
def test_move_leaves_the_source_directory_empty(tmp_path, storage_root, superuser):
    incoming = tmp_path / "incoming"
    _write_video(incoming / "one.mp4")

    call_command(
        "ingest_directory", incoming, class_name="A", theme="B", move=True, stdout=StringIO()
    )

    assert not (incoming / "one.mp4").exists()
    assert Video.objects.count() == 1


@ffmpeg_required
@pytest.mark.django_db
def test_recursive_is_opt_in(tmp_path, storage_root, superuser):
    incoming = tmp_path / "incoming"
    _write_video(incoming / "top.mp4")
    _write_video(incoming / "nested" / "deep.mp4")

    call_command("ingest_directory", incoming, class_name="A", theme="B", stdout=StringIO())
    assert Video.objects.count() == 1

    call_command(
        "ingest_directory", incoming, class_name="A", theme="B", recursive=True, stdout=StringIO()
    )
    assert Video.objects.count() == 2


@pytest.mark.django_db
def test_refuses_a_missing_directory(tmp_path, storage_root, superuser):
    with pytest.raises(CommandError, match="Not a directory"):
        call_command("ingest_directory", tmp_path / "nope", class_name="A", theme="B")


@pytest.mark.django_db
def test_requires_an_unambiguous_user(tmp_path, storage_root, superuser):
    User.objects.create_superuser(username="second", password="secret123!")
    incoming = tmp_path / "incoming"
    incoming.mkdir()

    with pytest.raises(CommandError, match="--user"):
        call_command("ingest_directory", incoming, class_name="A", theme="B")
