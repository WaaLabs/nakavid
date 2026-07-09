from django import forms
from django.db import models
from django.utils.text import get_valid_filename, slugify

from apps.library.models import Clip, Tag, TagCategory, Video


class TypeAIngestMetadataForm(forms.Form):
    class_name = forms.CharField(max_length=120, label="Class")
    theme = forms.CharField(max_length=120, label="Theme")
    recorded_at = forms.DateField(label="Date")
    filename = forms.CharField(max_length=255, label="Filename")
    upload_length = forms.IntegerField(min_value=1, label="Upload length")

    def clean_filename(self):
        filename = self.cleaned_data["filename"]
        if not filename.strip():
            raise forms.ValidationError("A filename is required.")
        return get_valid_filename(filename.strip())


class TypeBIngestForm(forms.Form):
    video_file = forms.FileField(
        label="Video file",
        help_text="Type B in-the-moment clip (under 5 minutes).",
    )
    class_name = forms.CharField(max_length=120, label="Class")
    theme = forms.CharField(max_length=120, label="Theme")
    recorded_at = forms.DateField(label="Date")

    def clean_video_file(self):
        uploaded = self.cleaned_data["video_file"]
        if not uploaded.name:
            raise forms.ValidationError("A video file is required.")
        return uploaded

    def cleaned_filename(self) -> str:
        uploaded = self.cleaned_data["video_file"]
        return get_valid_filename(uploaded.name)


class SourceVideosFilterForm(forms.Form):
    class_name = forms.CharField(max_length=120, required=False, label="Class")
    recorded_date = forms.DateField(required=False, label="Date")


class ClipsBrowserFilterForm(forms.Form):
    class_name = forms.CharField(max_length=120, required=False, label="Class")
    recorded_date = forms.DateField(required=False, label="Date")
    min_score = forms.IntegerField(
        required=False,
        min_value=0,
        max_value=100,
        label="Min highlight score",
    )


class TagCategoryForm(forms.ModelForm):
    class Meta:
        model = TagCategory
        fields = ("name", "description")


class TagForm(forms.ModelForm):
    class Meta:
        model = Tag
        fields = ("label", "slug", "category")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["slug"].required = False
        self.fields["category"].required = False
        self.fields["category"].queryset = TagCategory.objects.order_by("name")

    def clean_slug(self) -> str:
        slug = (self.cleaned_data.get("slug") or "").strip()
        if not slug:
            label = (self.cleaned_data.get("label") or "").strip()
            slug = slugify(label)
        if not slug:
            raise forms.ValidationError("Enter a label so a slug can be generated.")
        return slug


class BulkTagForm(forms.Form):
    class Action(models.TextChoices):
        ADD = "add", "Add tags"
        REMOVE = "remove", "Remove tags"

    videos = forms.ModelMultipleChoiceField(
        queryset=Video.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Videos",
    )
    clips = forms.ModelMultipleChoiceField(
        queryset=Clip.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Clips",
    )
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.none(),
        required=True,
        widget=forms.CheckboxSelectMultiple,
        label="Tags",
    )
    action = forms.ChoiceField(choices=Action.choices, initial=Action.ADD, label="Action")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["videos"].queryset = Video.objects.order_by("-recorded_at", "-id")
        self.fields["clips"].queryset = Clip.objects.select_related("video").order_by(
            "-video__recorded_at",
            "-highlight_score",
            "-id",
        )
        self.fields["tags"].queryset = Tag.objects.select_related("category").order_by(
            "label",
            "slug",
        )
        self.fields["videos"].label_from_instance = self._video_label
        self.fields["clips"].label_from_instance = self._clip_label
        self.fields["tags"].label_from_instance = self._tag_label

    @staticmethod
    def _video_label(video: Video) -> str:
        date_label = video.recorded_at.date().isoformat()
        return f"{video.title} · {video.class_name} · {date_label}"

    @staticmethod
    def _clip_label(clip: Clip) -> str:
        start = clip.start_seconds
        end = clip.end_seconds
        return f"{clip.video.title} [{start}–{end}s] · score {clip.highlight_score}"

    @staticmethod
    def _tag_label(tag: Tag) -> str:
        if tag.category_id:
            return f"{tag.label} ({tag.category.name})"
        return tag.label

    def clean(self):
        cleaned = super().clean()
        videos = cleaned.get("videos") or Video.objects.none()
        clips = cleaned.get("clips") or Clip.objects.none()
        if not videos and not clips:
            raise forms.ValidationError("Select at least one video or clip.")
        return cleaned


class CombineBuilderSubmitForm(forms.Form):
    title = forms.CharField(max_length=255, label="Combine title")
    clip_ids = forms.JSONField(label="Ordered clip IDs")

    def clean_clip_ids(self) -> list[int]:
        raw = self.cleaned_data.get("clip_ids")
        if not isinstance(raw, list):
            raise forms.ValidationError("clip_ids must be a JSON array.")
        if not raw:
            raise forms.ValidationError("Select at least one clip.")
        clip_ids: list[int] = []
        for index, value in enumerate(raw):
            if isinstance(value, bool) or not isinstance(value, int):
                raise forms.ValidationError(f"Invalid clip id at index {index}.")
            clip_ids.append(value)
        if len(set(clip_ids)) != len(clip_ids):
            raise forms.ValidationError("Each clip may appear only once.")
        existing = set(Clip.objects.filter(pk__in=clip_ids).values_list("pk", flat=True))
        missing = [clip_id for clip_id in clip_ids if clip_id not in existing]
        if missing:
            missing_label = ", ".join(str(item) for item in missing)
            raise forms.ValidationError(f"Unknown clip ids: {missing_label}")
        return clip_ids
