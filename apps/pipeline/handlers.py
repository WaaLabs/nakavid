from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.db import transaction

from apps.library.models import Clip, Combine, Video
from apps.library.storage_paths import (
    build_combine_relative_path,
    build_contact_sheet_relative_path,
    build_highlight_relative_paths,
    build_playback_relative_path,
    build_short_thumbnail_relative_path,
    to_absolute_storage_path,
)
from apps.pipeline.combine_export import CombineExportError, run_ffmpeg_concat
from apps.pipeline.contact_sheet import plan_contact_sheet, run_ffmpeg_contact_sheet
from apps.pipeline.enqueue import (
    enqueue_clip_extraction_job,
    enqueue_contact_sheet_job,
    enqueue_score_job,
    enqueue_transcode_job,
)
from apps.pipeline.extraction import (
    ClipExtractionError,
    run_ffmpeg_thumbnail,
    run_ffmpeg_trim,
    select_clip_segments,
)
from apps.pipeline.models import Job
from apps.pipeline.probe import needs_web_transcode, run_ffprobe
from apps.pipeline.scoring import run_segment_scoring, scoring_params_from_job
from apps.pipeline.transcode import run_ffmpeg_web_transcode


def _video_file_path(video: Video) -> Path:
    return _storage_path_to_file_path(video.source_path)


def _playback_file_path(video: Video) -> Path:
    """The browser-safe file when one exists, else the original.

    The transcode stage writes an 8-bit H.264 copy precisely so downstream
    output is playable in a browser. Anything producing files for playback
    must start from that copy — cutting from a 10-bit HDR original yields
    H.264 High 10 clips, which browsers decode as a black frame.
    """
    if video.playback_path:
        return _storage_path_to_file_path(video.playback_path)
    return _video_file_path(video)


def _storage_path_to_relative(storage_path: str) -> str:
    return storage_path.removeprefix("/nakavid/").lstrip("/")


def _storage_path_to_file_path(storage_path: str) -> Path:
    storage_root = Path(settings.NAKAVID_STORAGE_ROOT)
    return storage_root / _storage_path_to_relative(storage_path)


def handle_probe(job: Job) -> None:
    video = job.video
    probe_result = run_ffprobe(_video_file_path(video))

    with transaction.atomic():
        video.duration_seconds = probe_result.duration_seconds
        video.orientation = probe_result.orientation
        video.video_codec = probe_result.video_codec
        video.width = probe_result.width
        video.height = probe_result.height
        video.save(
            update_fields=[
                "duration_seconds",
                "orientation",
                "video_codec",
                "width",
                "height",
                "updated_at",
            ]
        )

        if video.video_type == Video.VideoType.TYPE_B:
            clip = video.clips.order_by("id").first()
            if clip is not None:
                clip.end_seconds = Decimal(probe_result.duration_seconds)
                clip.save(update_fields=["end_seconds", "updated_at"])

        # Both types need a browser-safe rendition and a score. Short
        # recordings used to get neither: a phone clip in 10-bit HEVC stayed
        # unplayable, and with no score there was nothing to sort or filter on.
        if needs_web_transcode(
            codec_name=probe_result.video_codec,
            pixel_format=probe_result.pixel_format,
        ):
            enqueue_transcode_job(video=video)
        else:
            if video.video_type == Video.VideoType.TYPE_A:
                enqueue_contact_sheet_job(video=video)
            enqueue_score_job(video=video)


def handle_transcode(job: Job) -> None:
    """Produce a browser-safe H.264 rendition for a non-playable source, then score."""
    video = job.video
    storage_root = Path(settings.NAKAVID_STORAGE_ROOT)
    relative_playback = build_playback_relative_path(_storage_path_to_relative(video.source_path))
    target_file_path = storage_root / relative_playback

    run_ffmpeg_web_transcode(
        source_path=_video_file_path(video),
        target_path=target_file_path,
    )

    video.playback_path = to_absolute_storage_path(storage_root, relative_playback)
    video.save(update_fields=["playback_path", "updated_at"])

    if video.video_type == Video.VideoType.TYPE_B:
        # A short recording is its own clip, and that clip pointed at the
        # original file — so transcoding produced a browser-safe rendition
        # nothing ever served. Extracted clips already point at their own
        # playable file; point this one at its rendition too, which also keeps
        # combine exports from mixing codecs.
        clip = video.clips.order_by("id").first()
        if clip is not None and clip.storage_path == video.source_path:
            clip.storage_path = video.playback_path
            clip.save(update_fields=["storage_path", "updated_at"])

    # A contact sheet only serves the tuning page, which tunes extraction from
    # long recordings. A short recording is already a clip, so it just needs a
    # score.
    if video.video_type == Video.VideoType.TYPE_A:
        enqueue_contact_sheet_job(video=video)
    enqueue_score_job(video=video)


def handle_contact_sheet(job: Job) -> None:
    """Render the recording's sprite of evenly spaced frames."""
    video = job.video
    storage_root = Path(settings.NAKAVID_STORAGE_ROOT)
    relative_sheet = build_contact_sheet_relative_path(_storage_path_to_relative(video.source_path))
    layout = plan_contact_sheet(duration_seconds=video.duration_seconds)

    run_ffmpeg_contact_sheet(
        source_path=_playback_file_path(video),
        target_path=storage_root / relative_sheet,
        layout=layout,
    )

    video.contact_sheet_path = to_absolute_storage_path(storage_root, relative_sheet)
    video.contact_sheet_interval_seconds = layout.interval_seconds
    video.contact_sheet_columns = layout.columns
    video.contact_sheet_tile_count = layout.tile_count
    video.contact_sheet_tile_width = layout.tile_width
    video.save(
        update_fields=[
            "contact_sheet_path",
            "contact_sheet_interval_seconds",
            "contact_sheet_columns",
            "contact_sheet_tile_count",
            "contact_sheet_tile_width",
            "updated_at",
        ]
    )


def handle_ingest(job: Job) -> None:
    """Skeleton handler — real ingest pipeline stages land in later issues."""


def _mark_combine_error(combine: Combine) -> None:
    combine.status = Combine.Status.ERROR
    combine.save(update_fields=["status", "updated_at"])


def handle_combine_export(job: Job) -> None:
    combine = job.combine
    if combine is None:
        raise CombineExportError("Combine export job is missing a combine")

    combine.status = Combine.Status.PROCESSING
    combine.save(update_fields=["status", "updated_at"])

    try:
        clip_paths = [
            _storage_path_to_file_path(combine_clip.clip.storage_path)
            for combine_clip in combine.combine_clips.select_related("clip").order_by("position")
        ]
        relative_output_path = build_combine_relative_path(
            title=combine.title,
            created_at=combine.created_at,
        )
        storage_root = Path(settings.NAKAVID_STORAGE_ROOT)
        absolute_output_path = to_absolute_storage_path(storage_root, relative_output_path)
        output_file_path = storage_root / relative_output_path

        run_ffmpeg_concat(input_paths=clip_paths, target_path=output_file_path)

        combine.output_path = absolute_output_path
        combine.status = Combine.Status.DONE
        combine.save(update_fields=["output_path", "status", "updated_at"])
    except CombineExportError:
        _mark_combine_error(combine)
        raise
    except Exception:
        _mark_combine_error(combine)
        raise


def handle_clip_extraction(job: Job) -> None:
    video = job.video
    if video.video_type != Video.VideoType.TYPE_A:
        return

    params = scoring_params_from_job(job)
    if not video.energy_curve:
        raise ClipExtractionError(
            f"Video {video.pk} has no energy curve; run the score stage first"
        )

    source_stem = Path(video.source_path).stem
    duration_seconds = float(video.duration_seconds)
    selections = select_clip_segments(
        energy_curve=video.energy_curve,
        params=params,
        duration_seconds=duration_seconds,
    )

    source_file_path = _playback_file_path(video)
    storage_root = Path(settings.NAKAVID_STORAGE_ROOT)
    video_tag_ids = list(video.tags.values_list("id", flat=True))

    with transaction.atomic():
        video.clips.all().delete()
        for clip_index, selection in enumerate(selections, start=1):
            relative_video_path, relative_thumbnail_path = build_highlight_relative_paths(
                recorded_at=video.recorded_at,
                class_name=video.class_name,
                theme=video.theme,
                source_stem=source_stem,
                clip_index=clip_index,
            )
            absolute_video_path = to_absolute_storage_path(storage_root, relative_video_path)
            absolute_thumbnail_path = to_absolute_storage_path(
                storage_root, relative_thumbnail_path
            )
            clip_file_path = storage_root / relative_video_path
            thumbnail_file_path = storage_root / relative_thumbnail_path

            run_ffmpeg_trim(
                source_path=source_file_path,
                target_path=clip_file_path,
                start_seconds=selection.start_seconds,
                end_seconds=selection.end_seconds,
            )
            run_ffmpeg_thumbnail(
                source_path=clip_file_path,
                target_path=thumbnail_file_path,
                at_seconds=max(0.0, (selection.end_seconds - selection.start_seconds) / 2.0),
            )

            clip = Clip.objects.create(
                video=video,
                storage_path=absolute_video_path,
                thumbnail_path=absolute_thumbnail_path,
                start_seconds=Decimal(f"{selection.start_seconds:.3f}"),
                end_seconds=Decimal(f"{selection.end_seconds:.3f}"),
                highlight_score=int(round(selection.score)),
                energy_curve=selection.energy_curve,
                created_by=video.created_by,
            )
            if video_tag_ids:
                clip.tags.set(video_tag_ids)


def _score_short_recording(*, video: Video, params) -> None:
    """Score a short recording in place — it is already its own clip.

    No extraction follows, because there is nothing to cut. The score exists so
    short recordings can be sorted and filtered alongside extracted clips, and
    so Nebla has a ranking signal for them later.
    """
    result = run_segment_scoring(
        video_path=_playback_file_path(video),
        params=params,
        duration_seconds=video.duration_seconds,
    )

    clip = video.clips.order_by("id").first()
    thumbnail_path = ""
    if clip is not None and not clip.thumbnail_path:
        relative_thumbnail = build_short_thumbnail_relative_path(
            _storage_path_to_relative(video.source_path)
        )
        storage_root = Path(settings.NAKAVID_STORAGE_ROOT)
        run_ffmpeg_thumbnail(
            source_path=_playback_file_path(video),
            target_path=storage_root / relative_thumbnail,
            at_seconds=max(0.0, video.duration_seconds / 2.0),
        )
        thumbnail_path = to_absolute_storage_path(storage_root, relative_thumbnail)

    with transaction.atomic():
        video.energy_curve = result.energy_curve
        video.highlight_score = result.highlight_score
        video.save(update_fields=["energy_curve", "highlight_score", "updated_at"])

        if clip is not None:
            clip.highlight_score = result.highlight_score
            clip.energy_curve = result.energy_curve
            if thumbnail_path:
                clip.thumbnail_path = thumbnail_path
            clip.save(
                update_fields=[
                    "highlight_score",
                    "energy_curve",
                    "thumbnail_path",
                    "updated_at",
                ]
            )


def handle_score(job: Job) -> None:
    video = job.video
    params = scoring_params_from_job(job)
    if video.video_type == Video.VideoType.TYPE_B:
        _score_short_recording(video=video, params=params)
        return

    result = run_segment_scoring(
        video_path=_video_file_path(video),
        params=params,
        duration_seconds=video.duration_seconds,
    )

    with transaction.atomic():
        # The curve lives on the video, not on a placeholder clip row. That row
        # was found by matching storage_path against the source, was deleted by
        # extraction, and was the thing a re-score corrupted.
        video.energy_curve = result.energy_curve
        video.highlight_score = result.highlight_score
        video.save(update_fields=["energy_curve", "highlight_score", "updated_at"])
        enqueue_clip_extraction_job(video=video, scoring_params_id=params.pk)


JOB_HANDLERS = {
    Job.JobType.PROBE: handle_probe,
    Job.JobType.INGEST: handle_ingest,
    Job.JobType.TRANSCODE: handle_transcode,
    Job.JobType.CLIP_EXTRACTION: handle_clip_extraction,
    Job.JobType.SCORE: handle_score,
    Job.JobType.CONTACT_SHEET: handle_contact_sheet,
    Job.JobType.COMBINE_EXPORT: handle_combine_export,
}


def dispatch_job(job: Job) -> None:
    handler = JOB_HANDLERS[job.job_type]
    handler(job)
