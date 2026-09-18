"""Queue a clip-extraction job for one video against a specific ScoringParams row.

For comparing extraction approaches (e.g. fixed vs. variable clip length) on
an already-scored video without touching the active default that new videos
pick up automatically. handle_clip_extraction only ever replaces clips from
its own scoring_params row, so a run queued here coexists with the video's
existing clips instead of replacing them — see Clip.scoring_params.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.library.models import Video
from apps.pipeline.enqueue import enqueue_clip_extraction_job
from apps.pipeline.models import ScoringParams


class Command(BaseCommand):
    help = (
        "Queue a clip-extraction job for one video against a specific ScoringParams "
        "row, for comparing extraction approaches side by side."
    )

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
        if not video.energy_curve:
            raise CommandError(
                f"Video {video_id} has no energy curve yet — run the score stage first"
            )
        try:
            params = ScoringParams.objects.get(pk=scoring_params_id)
        except ScoringParams.DoesNotExist as exc:
            raise CommandError(f"No ScoringParams with id {scoring_params_id}") from exc

        job = enqueue_clip_extraction_job(video=video, scoring_params_id=params.pk)
        self.stdout.write(
            f"Queued clip-extraction job {job.pk} for video {video.pk} "
            f"using ScoringParams #{params.pk} ({params.get_clip_length_mode_display()})"
        )
