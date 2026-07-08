from django.contrib.auth import get_user_model
from django.db import models

User = get_user_model()


class Video(models.Model):
    class VideoType(models.TextChoices):
        TYPE_A = "type_a", "Type A"
        TYPE_B = "type_b", "Type B"

    class Orientation(models.TextChoices):
        LANDSCAPE = "landscape", "Landscape"
        PORTRAIT = "portrait", "Portrait"
        SQUARE = "square", "Square"
        MIXED = "mixed", "Mixed"

    title = models.CharField(max_length=255)
    source_path = models.CharField(max_length=1024, unique=True)
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
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="videos")
    tags = models.ManyToManyField("Tag", related_name="videos", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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
