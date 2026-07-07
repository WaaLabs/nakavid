from django import forms
from django.utils.text import get_valid_filename


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


class ClipsBrowserFilterForm(forms.Form):
    class_name = forms.CharField(max_length=120, required=False, label="Class")
    recorded_date = forms.DateField(required=False, label="Date")
    min_score = forms.IntegerField(
        required=False,
        min_value=0,
        max_value=100,
        label="Min highlight score",
    )
