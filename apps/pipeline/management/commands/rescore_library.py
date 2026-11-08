"""Re-apply the active scoring params to every scored video's stored curve.

Weights and saturation points are arithmetic over the per-window signals the
score stage already stored, so changing them doesn't need a re-scan — only a
re-score of the stored numbers and, for long recordings, a re-run of clip
selection. The scoring-tuning page does this for one recording at a time;
this does it for the library.

Long recordings get a clip-extraction job for every ScoringParams row that
already has clips on them (so a fixed run and a variable eval run are both
rebuilt), or the active row if none do. Extraction replaces a row's clips, and
a rating belongs to a clip row, so a video with any rated clip is skipped
rather than silently losing those ratings — pass --include-rated to rebuild it
anyway. Short recordings are their own clip: the score is updated in place
and nothing is cut. Stills are scored separately and are not touched.

Idempotent: re-running with unchanged params rewrites the same scores.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.library.models import Video
from apps.pipeline.enqueue import enqueue_clip_extraction_job
from apps.pipeline.scoring import get_active_scoring_params, rescore_energy_curve


def _highlight_score(curve: list[dict]) -> int:
    return max(0, min(max((int(round(p.get("score", 0.0))) for p in curve), default=0), 100))


class Command(BaseCommand):
    help = "Re-score every scored video from its stored signals with the active params."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without touching the DB or the queue.",
        )
        parser.add_argument(
            "--include-rated",
            action="store_true",
            help="Also rebuild long recordings whose clips carry ratings (they are lost).",
        )

    def handle(self, *args, **options) -> None:
        dry_run: bool = options["dry_run"]
        include_rated: bool = options["include_rated"]
        params = get_active_scoring_params()

        rescored = 0
        jobs_queued = 0
        skipped_rated: list[int] = []
        changes: list[tuple[int, int, int]] = []

        videos = Video.objects.exclude(energy_curve=[]).prefetch_related("clips").order_by("pk")
        for video in videos:
            clips = list(video.clips.all())
            if (
                video.video_type == Video.VideoType.TYPE_A
                and not include_rated
                and any(clip.rating is not None for clip in clips)
            ):
                skipped_rated.append(video.pk)
                continue

            curve = rescore_energy_curve(energy_curve=video.energy_curve, params=params)
            new_score = _highlight_score(curve)
            changes.append((video.pk, video.highlight_score, new_score))
            rescored += 1

            params_ids: set[int] = set()
            if video.video_type == Video.VideoType.TYPE_A:
                params_ids = {c.scoring_params_id for c in clips if c.scoring_params_id} or {
                    params.pk
                }
                jobs_queued += len(params_ids)

            if dry_run:
                continue

            with transaction.atomic():
                video.energy_curve = curve
                video.highlight_score = new_score
                video.save(update_fields=["energy_curve", "highlight_score", "updated_at"])
                if video.video_type == Video.VideoType.TYPE_B:
                    for clip in clips:
                        clip.energy_curve = curve
                        clip.highlight_score = new_score
                        clip.save(update_fields=["energy_curve", "highlight_score", "updated_at"])
                for params_id in sorted(params_ids):
                    enqueue_clip_extraction_job(video=video, scoring_params_id=params_id)

        verbosity = int(options.get("verbosity", 1))
        if verbosity >= 2:
            for video_id, old, new in changes:
                self.stdout.write(f"  video {video_id}: highlight {old} -> {new}")

        prefix = "Would re-score" if dry_run else "Re-scored"
        self.stdout.write(
            f"{prefix} {rescored} video(s) with ScoringParams #{params.pk}; "
            f"{jobs_queued} clip-extraction job(s) {'would be ' if dry_run else ''}queued; "
            f"{len(skipped_rated)} skipped (rated clips)"
            + (f": {', '.join(map(str, skipped_rated))}" if skipped_rated else "")
        )
