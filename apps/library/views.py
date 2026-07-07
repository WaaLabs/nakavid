from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.library.duration import probe_duration_seconds
from apps.library.forms import ClipsBrowserFilterForm, TypeBIngestForm
from apps.library.models import Clip, Video
from apps.library.storage_paths import (
    build_originals_relative_path,
    to_absolute_storage_path,
    to_accel_redirect_path,
)


def _write_uploaded_file(*, destination: Path, uploaded_file) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as handle:
        for chunk in uploaded_file.chunks():
            handle.write(chunk)


@login_required
def type_b_ingest(request):
    if request.method == "POST":
        form = TypeBIngestForm(request.POST, request.FILES)
        if form.is_valid():
            relative_path = build_originals_relative_path(
                recorded_at=form.cleaned_data["recorded_at"],
                class_name=form.cleaned_data["class_name"],
                theme=form.cleaned_data["theme"],
                filename=form.cleaned_filename(),
            )
            storage_root = Path(settings.NAKAVID_STORAGE_ROOT)
            destination = storage_root / relative_path
            source_path = to_absolute_storage_path(storage_root, relative_path)

            _write_uploaded_file(
                destination=destination,
                uploaded_file=form.cleaned_data["video_file"],
            )
            duration_seconds = probe_duration_seconds(destination)
            title = Path(form.cleaned_filename()).stem
            recorded_at = timezone.make_aware(
                datetime.combine(form.cleaned_data["recorded_at"], time.min)
            )

            with transaction.atomic():
                video = Video.objects.create(
                    title=title,
                    source_path=source_path,
                    video_type=Video.VideoType.TYPE_B,
                    orientation=Video.Orientation.MIXED,
                    class_name=form.cleaned_data["class_name"],
                    theme=form.cleaned_data["theme"],
                    recorded_at=recorded_at,
                    duration_seconds=duration_seconds,
                    is_private=True,
                    created_by=request.user,
                )
                Clip.objects.create(
                    video=video,
                    storage_path=source_path,
                    start_seconds=Decimal("0.000"),
                    end_seconds=Decimal(duration_seconds),
                    created_by=request.user,
                )

            messages.success(request, f"Uploaded {title} to the library.")
            return redirect("type-b-ingest")
    else:
        form = TypeBIngestForm()

    return render(request, "library/type_b_ingest.html", {"form": form})


def _filtered_clips(*, form: ClipsBrowserFilterForm):
    clips = Clip.objects.select_related("video").order_by(
        "-video__recorded_at",
        "-highlight_score",
        "-id",
    )
    class_name = form.cleaned_data.get("class_name")
    recorded_date = form.cleaned_data.get("recorded_date")
    min_score = form.cleaned_data.get("min_score")

    if class_name:
        clips = clips.filter(video__class_name__iexact=class_name)
    if recorded_date:
        clips = clips.filter(video__recorded_at__date=recorded_date)
    if min_score is not None:
        clips = clips.filter(highlight_score__gte=min_score)
    return clips


@login_required
def clips_browser(request):
    form = ClipsBrowserFilterForm(request.GET)
    clips = _filtered_clips(form=form) if form.is_valid() else Clip.objects.none()

    return render(
        request,
        "library/clips_browser.html",
        {
            "form": form,
            "clips": clips,
        },
    )


@login_required
def clip_stream(request, clip_id: int):
    clip = get_object_or_404(Clip.objects.select_related("video"), pk=clip_id)
    response = HttpResponse()
    response["X-Accel-Redirect"] = to_accel_redirect_path(clip.storage_path)
    response["Content-Type"] = ""
    return response
