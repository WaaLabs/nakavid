from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from apps.library.models import Video
from apps.library.storage_paths import HIGHLIGHTS_PREFIX, slug_segment
from apps.pipeline.models import ScoringParams

CLIP_EXPAND_SECONDS = 3.0


class ClipExtractionError(RuntimeError):
    """Raised when clip extraction cannot complete."""


@dataclass(frozen=True)
class ClipSelection:
    start_seconds: float
    end_seconds: float
    score: float
    energy_curve: list[dict]


def _normalize_segment(
    *, start_seconds: float, end_seconds: float, duration_seconds: float
) -> tuple[float, float]:
    start = max(0.0, min(start_seconds, duration_seconds))
    end = max(start, min(end_seconds, duration_seconds))
    if end <= start:
        end = min(duration_seconds, start + 0.001)
    return start, end


def _expand_segment(
    *,
    center_seconds: float,
    duration_seconds: float,
    min_length_seconds: float,
) -> tuple[float, float]:
    start_seconds = center_seconds - CLIP_EXPAND_SECONDS
    end_seconds = center_seconds + CLIP_EXPAND_SECONDS
    start, end = _normalize_segment(
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        duration_seconds=duration_seconds,
    )
    length = end - start
    if length >= min_length_seconds:
        return start, end

    deficit = min_length_seconds - length
    start = max(0.0, start - (deficit / 2.0))
    end = min(duration_seconds, end + (deficit / 2.0))
    if (end - start) < min_length_seconds and start == 0.0:
        end = min(duration_seconds, start + min_length_seconds)
    if (end - start) < min_length_seconds and end == duration_seconds:
        start = max(0.0, end - min_length_seconds)
    return _normalize_segment(
        start_seconds=start,
        end_seconds=end,
        duration_seconds=duration_seconds,
    )


def _motion_score(point: dict) -> float:
    signals = point.get("signals")
    if not isinstance(signals, dict):
        return float("inf")
    value = signals.get("motion_energy")
    if value is None:
        return float("inf")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("inf")


def _snap_boundary(
    *,
    target_seconds: float,
    energy_curve: list[dict],
    radius_seconds: float,
    kind: str,
) -> float:
    candidates: list[tuple[float, float, float]] = []
    for point in energy_curve:
        boundary = float(point["start"] if kind == "start" else point["end"])
        distance = abs(boundary - target_seconds)
        if distance > radius_seconds:
            continue
        candidates.append((_motion_score(point), distance, boundary))
    if not candidates:
        return target_seconds
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    return candidates[0][2]


def _segments_overlap(
    first: tuple[float, float],
    second: tuple[float, float],
    *,
    min_gap_seconds: float,
) -> bool:
    first_start, first_end = first
    second_start, second_end = second
    return not (
        first_end + min_gap_seconds <= second_start or second_end + min_gap_seconds <= first_start
    )


def _clip_energy_curve(
    *, energy_curve: list[dict], start_seconds: float, end_seconds: float
) -> list[dict]:
    return [
        point
        for point in energy_curve
        if float(point["end"]) > start_seconds and float(point["start"]) < end_seconds
    ]


def select_clip_segments(
    *,
    energy_curve: list[dict],
    params: ScoringParams,
    duration_seconds: float,
) -> list[ClipSelection]:
    if not energy_curve:
        return []

    min_length_seconds = float(params.min_clip_length_seconds)
    min_gap_seconds = float(params.min_gap_seconds)
    snap_radius = float(params.step_seconds)
    candidates = sorted(
        energy_curve,
        key=lambda point: (float(point.get("score", 0.0)), -float(point["start"])),
        reverse=True,
    )
    selections: list[ClipSelection] = []

    for point in candidates:
        if len(selections) >= int(params.peak_count):
            break
        center = (float(point["start"]) + float(point["end"])) / 2.0
        start, end = _expand_segment(
            center_seconds=center,
            duration_seconds=duration_seconds,
            min_length_seconds=min_length_seconds,
        )
        start = _snap_boundary(
            target_seconds=start,
            energy_curve=energy_curve,
            radius_seconds=snap_radius,
            kind="start",
        )
        end = _snap_boundary(
            target_seconds=end,
            energy_curve=energy_curve,
            radius_seconds=snap_radius,
            kind="end",
        )
        start, end = _normalize_segment(
            start_seconds=start,
            end_seconds=end,
            duration_seconds=duration_seconds,
        )
        if (end - start) < min_length_seconds:
            continue
        segment = (start, end)
        if any(
            _segments_overlap(
                segment,
                (selection.start_seconds, selection.end_seconds),
                min_gap_seconds=min_gap_seconds,
            )
            for selection in selections
        ):
            continue
        selections.append(
            ClipSelection(
                start_seconds=round(start, 3),
                end_seconds=round(end, 3),
                score=float(point.get("score", 0.0)),
                energy_curve=_clip_energy_curve(
                    energy_curve=energy_curve,
                    start_seconds=start,
                    end_seconds=end,
                ),
            )
        )

    return sorted(selections, key=lambda selection: selection.start_seconds)


def build_highlight_relative_paths(
    *, video: Video, source_stem: str, clip_index: int
) -> tuple[str, str]:
    recorded_on = video.recorded_at.date()
    clip_stem = f"{source_stem}__clip_{clip_index:03d}"
    folder = (
        PurePosixPath(HIGHLIGHTS_PREFIX)
        / str(recorded_on.year)
        / f"{recorded_on.month:02d}"
        / (
            f"{recorded_on.strftime('%Y%m%d')}_{slug_segment(video.class_name)}"
            f"_{slug_segment(video.theme)}"
        )
    )
    video_relative_path = str(folder / f"{clip_stem}.mp4")
    thumbnail_relative_path = str(folder / f"{clip_stem}.jpg")
    return video_relative_path, thumbnail_relative_path


def run_ffmpeg_trim(
    *, source_path: Path, target_path: Path, start_seconds: float, end_seconds: float
) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.001, end_seconds - start_seconds)
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{start_seconds:.3f}",
        "-i",
        str(source_path),
        "-t",
        f"{duration:.3f}",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        str(target_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or "ffmpeg trim failed"
        raise ClipExtractionError(message) from exc


def run_ffmpeg_thumbnail(*, source_path: Path, target_path: Path, at_seconds: float) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{at_seconds:.3f}",
        "-i",
        str(source_path),
        "-frames:v",
        "1",
        str(target_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or "ffmpeg thumbnail failed"
        raise ClipExtractionError(message) from exc
