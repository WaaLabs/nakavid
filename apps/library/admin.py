from django.contrib import admin

from apps.library.models import Clip, Combine, Tag, TagCategory, Video


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


@admin.register(Video)
class VideoAdmin(admin.ModelAdmin):
    list_display = ("title", "video_type", "class_name", "theme", "recorded_at")
    list_filter = ("video_type", "class_name")
    search_fields = ("title", "class_name", "theme")
    filter_horizontal = ("tags",)


@admin.register(Clip)
class ClipAdmin(admin.ModelAdmin):
    list_display = ("id", "video", "start_seconds", "end_seconds", "highlight_score")
    list_filter = ("highlight_score",)
    filter_horizontal = ("tags",)


@admin.register(Combine)
class CombineAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "created_by", "created_at")
    list_filter = ("status",)
