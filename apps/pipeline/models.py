from django.db import models

from apps.library.models import Video


class ScoringParams(models.Model):
    face_weight = models.DecimalField(max_digits=4, decimal_places=3, default=0.250)
    smile_weight = models.DecimalField(max_digits=4, decimal_places=3, default=0.250)
    motion_weight = models.DecimalField(max_digits=4, decimal_places=3, default=0.250)
    audio_weight = models.DecimalField(max_digits=4, decimal_places=3, default=0.250)
    silence_penalty_weight = models.DecimalField(max_digits=4, decimal_places=3, default=0.100)
    silence_rms_threshold = models.DecimalField(max_digits=6, decimal_places=4, default=0.0100)
    window_size_seconds = models.PositiveSmallIntegerField(default=4)
    step_seconds = models.PositiveSmallIntegerField(default=2)
    min_clip_length_seconds = models.PositiveSmallIntegerField(default=4)
    min_gap_seconds = models.PositiveSmallIntegerField(default=2)
    peak_count = models.PositiveSmallIntegerField(default=8)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"ScoringParams #{self.pk}"


class Job(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        DONE = "done", "Done"
        ERROR = "error", "Error"

    class JobType(models.TextChoices):
        PROBE = "probe", "Probe"
        INGEST = "ingest", "Ingest"
        CLIP_EXTRACTION = "clip_extraction", "Clip Extraction"
        SCORE = "score", "Score"

    video = models.ForeignKey(Video, on_delete=models.CASCADE, related_name="jobs")
    scoring_params = models.ForeignKey(
        ScoringParams, on_delete=models.PROTECT, related_name="jobs", null=True, blank=True
    )
    job_type = models.CharField(max_length=32, choices=JobType.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    stderr = models.TextField(blank=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.get_job_type_display()} ({self.get_status_display()})"
