"""Compare what drove the score on rated-up vs rated-down clips and stills.

Read-only. The starting point for scoring tuning: if a signal is about the same
on thumbs-up and thumbs-down picks, its weight isn't buying anything; if a
thumbs-down pick scored as high as a thumbs-up one, something the scorer
rewards isn't what makes a good highlight. Clips are compared per
ScoringParams row, so a fixed-length run and a variable-length eval run on the
same recordings can be judged side by side.

The numbers only describe the ratings that exist. Whether a change they
suggest is an improvement is still the founder's call on real footage — see
AGENTS.md, "The scoring boundary".
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from django.core.management.base import BaseCommand

from apps.library.models import Clip, Still

# Below this many ratings on either side, a gap between the groups is as
# likely noise as signal. Reported, not enforced — the numbers still print.
MIN_RATINGS_PER_SIDE = 10

GROUPS: tuple[tuple[str, bool | None], ...] = (("up", True), ("down", False), ("unrated", None))

CLIP_SIGNALS = ("face_count", "smile_ratio", "motion_energy", "audio_rms")


@dataclass(frozen=True)
class Metric:
    label: str
    value: Callable[[object], float | None]
    digits: int


def _peak_signal(name: str) -> Callable[[object], float | None]:
    """A signal from the clip's highest-scoring window — what selection
    expanded the clip outward from, so what actually earned it the pick."""

    def read(clip: object) -> float | None:
        curve = getattr(clip, "energy_curve", None) or []
        if not curve:
            return None
        peak = max(curve, key=lambda point: float(point.get("score", 0.0)))
        value = (peak.get("signals") or {}).get(name)
        return float(value) if value is not None else None

    return read


CLIP_METRICS: tuple[Metric, ...] = (
    Metric("score", lambda clip: float(clip.highlight_score), 1),
    Metric("length s", lambda clip: float(clip.end_seconds - clip.start_seconds), 1),
    *(Metric(f"peak {name}", _peak_signal(name), 3) for name in CLIP_SIGNALS),
)

STILL_METRICS: tuple[Metric, ...] = (
    Metric("score", lambda still: float(still.quality_score), 1),
    Metric("faces", lambda still: float(still.face_count), 2),
    Metric("smiles", lambda still: float(still.smile_count), 2),
    Metric("sharpness", lambda still: float(still.sharpness), 1),
    Metric("obstructed %", lambda still: 100.0 if still.obstructed else 0.0, 0),
)


def _mean(values: Iterable[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return sum(present) / len(present) if present else None


def _format(value: float | None, digits: int) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def compare(items: list, metrics: tuple[Metric, ...]) -> list[list[str]]:
    """One row per metric: mean for up / down / unrated, then up minus down."""
    by_group = {
        label: [item for item in items if item.rating is rating] for label, rating in GROUPS
    }
    rows = [["", *(f"{label} (n={len(by_group[label])})" for label, _ in GROUPS), "up − down"]]
    for metric in metrics:
        means = {
            label: _mean(metric.value(item) for item in by_group[label]) for label, _ in GROUPS
        }
        gap = (
            means["up"] - means["down"]
            if means["up"] is not None and means["down"] is not None
            else None
        )
        rows.append(
            [
                metric.label,
                *(_format(means[label], metric.digits) for label, _ in GROUPS),
                "—" if gap is None else f"{gap:+.{metric.digits}f}",
            ]
        )
    return rows


def render_table(rows: list[list[str]]) -> str:
    widths = [max(len(row[col]) for row in rows) for col in range(len(rows[0]))]
    lines = []
    for index, row in enumerate(rows):
        cells = [row[0].ljust(widths[0])] + [
            cell.rjust(width) for cell, width in zip(row[1:], widths[1:], strict=True)
        ]
        lines.append("  ".join(cells))
        if index == 0:
            lines.append("  ".join("-" * width for width in widths))
    return "\n".join(lines)


def _thin_warning(items: list) -> str | None:
    up = sum(1 for item in items if item.rating is True)
    down = sum(1 for item in items if item.rating is False)
    if up >= MIN_RATINGS_PER_SIDE and down >= MIN_RATINGS_PER_SIDE:
        return None
    return (
        f"Only {up} up / {down} down rated — under {MIN_RATINGS_PER_SIDE} on a side, "
        "treat any gap as a hint, not a finding."
    )


class Command(BaseCommand):
    help = "Compare scoring signals on thumbs-up vs thumbs-down clips and stills (read-only)."

    def handle(self, *args, **options) -> None:
        clips = list(Clip.objects.select_related("scoring_params", "video").order_by("pk"))
        stills = list(Still.objects.order_by("pk"))

        self._section("Stills", stills, STILL_METRICS)

        runs: dict[str, list] = {}
        for clip in clips:
            if clip.video.video_type != clip.video.VideoType.TYPE_A:
                runs.setdefault("Short recordings (whole recording is the clip)", []).append(clip)
                continue
            params = clip.scoring_params
            if params is None:
                key = "Clips from before provenance was recorded"
            else:
                key = (
                    f"Clips — ScoringParams #{params.pk} ({params.get_clip_length_mode_display()})"
                )
            runs.setdefault(key, []).append(clip)

        if not runs:
            self.stdout.write("\nNo clips yet.")
        for title in sorted(runs):
            self._section(title, runs[title], CLIP_METRICS)

    def _section(self, title: str, items: list, metrics: tuple[Metric, ...]) -> None:
        self.stdout.write(f"\n{title}")
        self.stdout.write("=" * len(title))
        if not items:
            self.stdout.write("None yet.")
            return
        rated = sum(1 for item in items if item.rating is not None)
        up = sum(1 for item in items if item.rating is True)
        rate = f", {100 * up / rated:.0f}% of rated are up" if rated else ""
        self.stdout.write(f"{rated} of {len(items)} rated{rate}")
        warning = _thin_warning(items)
        if warning:
            self.stdout.write(warning)
        self.stdout.write(render_table(compare(items, metrics)))
