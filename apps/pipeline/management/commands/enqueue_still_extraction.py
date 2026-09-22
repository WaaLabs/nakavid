"""Queue a still-extraction job for one video against a specific ScoringParams row.

handle_score already enqueues still extraction automatically for every
newly-scored video, using whichever ScoringParams row did the scoring. This
command is for re-running an already-scored video against a *different*
ScoringParams row — comparing still_* tunables, the same pattern
enqueue_clip_extraction_eval uses for comparing clip-extraction approaches.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.library.models import Video
from apps.pipeline.enqueue import enqueue_still_extraction_job
from apps.pipeline.models import ScoringParams


class Command(BaseCommand):
    help = "Queue a still-extraction job for one video against a specific ScoringParams row."

    def add_arguments(self, parser) -> None:
        parser.add_argument("video_id", type=int)
        parser.add_argument("scoring_params_id", type=int)

    def handle(self, *args, **options) -> None:
        video_id: int = options["video_id"]
        scoring_params_id: int = options["scoring_params_id"]

        try:
            video = Video.objects.get(pk=video_id)
        except Video.DoesNotExist as exc:
            raise CommandError(f"No video with id {video_id}") from exc
        try:
            params = ScoringParams.objects.get(pk=scoring_params_id)
        except ScoringParams.DoesNotExist as exc:
            raise CommandError(f"No ScoringParams with id {scoring_params_id}") from exc

        job = enqueue_still_extraction_job(video=video, scoring_params_id=params.pk)
        self.stdout.write(
            f"Queued still-extraction job {job.pk} for video {video.pk} "
            f"using ScoringParams #{params.pk}"
        )
