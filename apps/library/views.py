from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.library.duration import probe_duration_seconds
from apps.library.forms import TypeBIngestForm
from apps.library.models import Clip, Video
from apps.library.storage_paths import (
    build_originals_relative_path,
    to_absolute_storage_path,
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
