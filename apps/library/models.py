from django.contrib.auth import get_user_model
from django.db import models

from apps.library.duration import format_duration_seconds, format_timecode_seconds

User = get_user_model()


class Video(models.Model):
    class VideoType(models.TextChoices):
        TYPE_A = "type_a", "Long recording"
        TYPE_B = "type_b", "Short recording"

    class Orientation(models.TextChoices):
        LANDSCAPE = "landscape", "Landscape"
        PORTRAIT = "portrait", "Portrait"
        SQUARE = "square", "Square"
        MIXED = "mixed", "Mixed"

    title = models.CharField(max_length=255)
    source_path = models.CharField(max_length=1024, unique=True)
    # Browser-safe H.264 rendition, set by the transcode stage when the source
    # codec (e.g. HEVC) can't stream directly. Empty means the source is served.
    playback_path = models.CharField(max_length=1024, blank=True, default="")
    video_type = models.CharField(max_length=16, choices=VideoType.choices)
    orientation = models.CharField(max_length=16, choices=Orientation.choices)
    class_name = models.CharField(max_length=120)
    theme = models.CharField(max_length=120)
    recorded_at = models.DateTimeField()
    duration_seconds = models.PositiveIntegerField()
    video_codec = models.CharField(max_length=64, blank=True)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    is_private = models.BooleanField(default=True)
    # Whole-video per-window signals from the scoring stage. Kept on the video
    # so selection can be re-run — and re-tuned — without re-scoring, which is
    # the expensive half. Clips carry their own slice of this.
    energy_curve = models.JSONField(default=list, blank=True)
    # Sprite of evenly spaced frames, used to preview any timestamp without
    # running ffmpeg in a request. Empty until the contact-sheet job runs.
    contact_sheet_path = models.CharField(max_length=1024, blank=True, default="")
    contact_sheet_interval_seconds = models.PositiveSmallIntegerField(default=0)
    contact_sheet_columns = models.PositiveSmallIntegerField(default=0)
    contact_sheet_tile_count = models.PositiveSmallIntegerField(default=0)
    contact_sheet_tile_width = models.PositiveSmallIntegerField(default=0)
    highlight_score = models.PositiveSmallIntegerField(default=0)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="videos")
    tags = models.ManyToManyField("Tag", related_name="videos", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def is_long_recording(self) -> bool:
        """Long recordings get split into clips; short ones are used as they are."""
        return self.video_type == Video.VideoType.TYPE_A

    def __str__(self) -> str:
        return self.title


class TagCategory(models.Model):
    name = models.CharField(max_length=80, unique=True)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "tag categories"

    def __str__(self) -> str:
        return self.name


class Tag(models.Model):
    slug = models.SlugField(max_length=80, unique=True)
    label = models.CharField(max_length=80)
    category = models.ForeignKey(
        TagCategory, on_delete=models.PROTECT, related_name="tags", null=True, blank=True
    )

    class Meta:
        ordering = ["label", "slug"]

    def __str__(self) -> str:
        return self.label


class Clip(models.Model):
    video = models.ForeignKey(Video, on_delete=models.CASCADE, related_name="clips")
    storage_path = models.CharField(max_length=1024, unique=True)
    thumbnail_path = models.CharField(max_length=1024, blank=True)
    start_seconds = models.DecimalField(max_digits=8, decimal_places=3)
    end_seconds = models.DecimalField(max_digits=8, decimal_places=3)
    highlight_score = models.PositiveSmallIntegerField(default=0)
    energy_curve = models.JSONField(default=list)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="clips")
    tags = models.ManyToManyField(Tag, related_name="clips", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_seconds__gt=models.F("start_seconds")),
                name="clip_end_after_start",
            ),
            models.CheckConstraint(
                condition=models.Q(highlight_score__gte=0, highlight_score__lte=100),
                name="clip_highlight_score_range",
            ),
        ]

    @property
    def duration_seconds(self) -> int:
        return int(self.end_seconds - self.start_seconds)

    @property
    def duration_label(self) -> str:
        return format_duration_seconds(self.duration_seconds)

    @property
    def source_start_label(self) -> str:
        """Where this clip starts in its source recording, as m:ss."""
        return format_timecode_seconds(int(self.start_seconds))

    @property
    def source_end_label(self) -> str:
        return format_timecode_seconds(int(self.end_seconds))

    @property
    def source_range_label(self) -> str:
        """The clip's span in source timecode, for tracing it back."""
        return f"{self.source_start_label}–{self.source_end_label}"

    def __str__(self) -> str:
        return f"{self.video.title} [{self.start_seconds}-{self.end_seconds}]"


class Combine(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        DONE = "done", "Done"
        ERROR = "error", "Error"

    title = models.CharField(max_length=255)
    output_path = models.CharField(max_length=1024, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="combines")
    clips = models.ManyToManyField(Clip, through="CombineClip", related_name="combines")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.title


class CombineClip(models.Model):
    combine = models.ForeignKey(Combine, on_delete=models.CASCADE, related_name="combine_clips")
    clip = models.ForeignKey(Clip, on_delete=models.CASCADE, related_name="combine_clips")
    position = models.PositiveSmallIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["combine", "position"],
                name="combine_clip_unique_position",
            ),
            models.UniqueConstraint(
                fields=["combine", "clip"],
                name="combine_clip_unique_clip",
            ),
        ]
        ordering = ["position"]

    def __str__(self) -> str:
        return f"{self.combine.title} #{self.position}"
