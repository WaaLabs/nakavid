from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.library.models import Clip, Combine, CombineClip, Video
from apps.library.storage_paths import build_combine_relative_path, to_absolute_storage_path
from apps.pipeline.combine_export import CombineExportError, run_ffmpeg_concat
from apps.pipeline.handlers import handle_combine_export
from apps.pipeline.models import Job
from apps.pipeline.worker import process_job

User = get_user_model()


@pytest.fixture
def storage_root(tmp_path, settings):
    settings.NAKAVID_STORAGE_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def user(db):
    return User.objects.create_user(username="combine-export", password="secret123!")


def _create_clip(*, storage_root: Path, user, suffix: str) -> Clip:
    recorded_at = timezone.make_aware(datetime(2026, 7, 1, 12, 0))
    video = Video.objects.create(
        title=f"source_{suffix}",
        source_path=to_absolute_storage_path(
            storage_root,
            f"originals/2026/07/20260701_a_animals/source_{suffix}.mp4",
        ),
        video_type=Video.VideoType.TYPE_A,
        orientation=Video.Orientation.LANDSCAPE,
        class_name="A",
        theme="Animals",
        recorded_at=recorded_at,
        duration_seconds=600,
        created_by=user,
    )
    clip_path = to_absolute_storage_path(
        storage_root,
        f"highlights/2026/07/20260701_a_animals/source_{suffix}__clip_001.mp4",
    )
    clip_file = storage_root / clip_path.removeprefix("/nakavid/")
    clip_file.parent.mkdir(parents=True, exist_ok=True)
    clip_file.write_bytes(b"clip-bytes")
    return Clip.objects.create(
        video=video,
        storage_path=clip_path,
        start_seconds=Decimal("10.000"),
        end_seconds=Decimal("40.000"),
        highlight_score=80,
        created_by=user,
    )


def _create_combine_job(*, storage_root: Path, user, clip_ids: list[int]) -> tuple[Combine, Job]:
    clips = [
        _create_clip(storage_root=storage_root, user=user, suffix=str(index)) for index in clip_ids
    ]
    combine = Combine.objects.create(title="Week 1 Highlights", created_by=user)
    for position, clip in enumerate(clips, start=1):
        CombineClip.objects.create(combine=combine, clip=clip, position=position)
    job = Job.objects.create(
        video=clips[0].video,
        combine=combine,
        job_type=Job.JobType.COMBINE_EXPORT,
        status=Job.Status.PROCESSING,
    )
    return combine, job


@pytest.mark.django_db
def test_build_combine_relative_path_uses_title_slug_and_date():
    relative_path = build_combine_relative_path(
        title="Week 1 Highlights",
        created_at=datetime(2026, 7, 8, 15, 30),
    )

    assert relative_path == "combines/week_1_highlights_20260708.mp4"


@pytest.mark.django_db
def test_handle_combine_export_updates_status_and_output_path(storage_root, user):
    combine, job = _create_combine_job(storage_root=storage_root, user=user, clip_ids=[1, 2])
    expected_relative_path = build_combine_relative_path(
        title=combine.title,
        created_at=combine.created_at,
    )
    expected_output_path = to_absolute_storage_path(storage_root, expected_relative_path)

    with patch("apps.pipeline.handlers.run_ffmpeg_concat") as run_concat:
        handle_combine_export(job)

    combine.refresh_from_db()
    assert combine.status == Combine.Status.DONE
    assert combine.output_path == expected_output_path
    run_concat.assert_called_once()
    call_kwargs = run_concat.call_args.kwargs
    assert len(call_kwargs["input_paths"]) == 2
    assert call_kwargs["target_path"] == storage_root / expected_relative_path


@pytest.mark.django_db
def test_handle_combine_export_preserves_clip_order(storage_root, user):
    first = _create_clip(storage_root=storage_root, user=user, suffix="first")
    second = _create_clip(storage_root=storage_root, user=user, suffix="second")
    combine = Combine.objects.create(title="Ordered Combine", created_by=user)
    CombineClip.objects.create(combine=combine, clip=second, position=1)
    CombineClip.objects.create(combine=combine, clip=first, position=2)
    job = Job.objects.create(
        video=second.video,
        combine=combine,
        job_type=Job.JobType.COMBINE_EXPORT,
        status=Job.Status.PROCESSING,
    )

    with patch("apps.pipeline.handlers.run_ffmpeg_concat") as run_concat:
        handle_combine_export(job)

    input_paths = run_concat.call_args.kwargs["input_paths"]
    assert [path.name for path in input_paths] == [
        "source_second__clip_001.mp4",
        "source_first__clip_001.mp4",
    ]


@pytest.mark.django_db
def test_handle_combine_export_sets_error_status_on_ffmpeg_failure(storage_root, user):
    combine, job = _create_combine_job(storage_root=storage_root, user=user, clip_ids=[1])

    with patch(
        "apps.pipeline.handlers.run_ffmpeg_concat",
        side_effect=CombineExportError("ffmpeg concat failed: invalid data"),
    ):
        with pytest.raises(CombineExportError, match="ffmpeg concat failed"):
            handle_combine_export(job)

    combine.refresh_from_db()
    assert combine.status == Combine.Status.ERROR
    assert combine.output_path == ""


@pytest.mark.django_db
def test_process_job_records_stderr_for_combine_export_failure(storage_root, user):
    combine, job = _create_combine_job(storage_root=storage_root, user=user, clip_ids=[1])

    with patch(
        "apps.pipeline.handlers.run_ffmpeg_concat",
        side_effect=CombineExportError("ffmpeg concat failed: invalid data"),
    ):
        process_job(job)

    job.refresh_from_db()
    combine.refresh_from_db()
    assert job.status == Job.Status.ERROR
    assert job.stderr == "ffmpeg concat failed: invalid data"
    assert combine.status == Combine.Status.ERROR


@pytest.mark.django_db
def test_run_ffmpeg_concat_invokes_ffmpeg_with_concat_demuxer(tmp_path):
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    output = tmp_path / "output.mp4"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    captured_list_contents = ""

    def capture_list_file(command, **kwargs):
        nonlocal captured_list_contents
        list_path = Path(command[command.index("-i") + 1])
        captured_list_contents = list_path.read_text(encoding="utf-8")

    with patch("apps.pipeline.combine_export.subprocess.run", side_effect=capture_list_file):
        run_ffmpeg_concat(input_paths=[first, second], target_path=output)

    assert f"file '{first}'" in captured_list_contents
    assert f"file '{second}'" in captured_list_contents


@pytest.mark.django_db
def test_run_ffmpeg_concat_raises_on_failure(tmp_path):
    clip = tmp_path / "clip.mp4"
    output = tmp_path / "output.mp4"
    clip.write_bytes(b"a")

    with patch(
        "apps.pipeline.combine_export.subprocess.run",
        side_effect=__import__("subprocess").CalledProcessError(
            1,
            ["ffmpeg"],
            stderr="invalid input",
        ),
    ):
        with pytest.raises(CombineExportError, match="invalid input"):
            run_ffmpeg_concat(input_paths=[clip], target_path=output)
