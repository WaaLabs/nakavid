from django import forms
from django.utils.text import get_valid_filename


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
