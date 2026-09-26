from django.contrib import admin

from apps.library.models import Clip, Combine, Still, Tag, TagCategory, Video
from apps.pipeline.enqueue import enqueue_transcode_job


@admin.register(TagCategory)
class TagCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "description")
    search_fields = ("name",)


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("label", "slug", "category")
    list_filter = ("category",)
    search_fields = ("label", "slug")
    prepopulated_fields = {"slug": ("label",)}
    autocomplete_fields = ("category",)


@admin.action(description="Re-queue transcode (picks up a rotation override)")
def requeue_transcode(modeladmin, request, queryset):
    for video in queryset:
        enqueue_transcode_job(video=video)
    modeladmin.message_user(request, f"Queued a transcode job for {queryset.count()} video(s).")


@admin.register(Video)
class VideoAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "video_type",
        "class_name",
        "theme",
        "recorded_at",
        "rotation_degrees",
        "rotation_override_degrees",
    )
    list_filter = ("video_type", "class_name")
    list_editable = ("rotation_override_degrees",)
    search_fields = ("title", "class_name", "theme")
    filter_horizontal = ("tags",)
    readonly_fields = ("rotation_degrees",)
    actions = (requeue_transcode,)


@admin.register(Clip)
class ClipAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "video",
        "start_seconds",
        "end_seconds",
        "highlight_score",
        "rating",
        "scoring_params",
    )
    list_filter = ("highlight_score", "rating", "scoring_params")
    filter_horizontal = ("tags",)


@admin.register(Still)
class StillAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "video",
        "capture_seconds",
        "quality_score",
        "rating",
        "face_count",
        "smile_count",
        "sharpness",
        "obstructed",
        "scoring_params",
    )
    list_filter = ("quality_score", "rating", "obstructed", "scoring_params")
    filter_horizontal = ("tags",)


@admin.register(Combine)
class CombineAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "created_by", "created_at")
    list_filter = ("status",)
