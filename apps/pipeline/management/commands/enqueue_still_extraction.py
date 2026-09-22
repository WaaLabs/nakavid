"""Queue a still-extraction job for one video against a specific ScoringParams row.

Stills are a new, still-tuning feature — triggered manually rather than
folded into the automatic pipeline yet, so existing videos aren't
reprocessed before the still_* thresholds have been validated on real
footage. See enqueue_clip_extraction_eval for the same pattern applied to
clip extraction.
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
