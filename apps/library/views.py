from __future__ import annotations

import json
from datetime import datetime, time
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.library.duration import format_duration_seconds, probe_duration_seconds
from apps.library.forms import (
    ClipsBrowserFilterForm,
    SourceVideosFilterForm,
    TypeAIngestMetadataForm,
    TypeBIngestForm,
)
from apps.library.models import Clip, Video
from apps.library.resumable_upload import (
    TUS_RESUMABLE_HEADER,
    TUS_VERSION,
    ResumableUploadError,
    append_chunk,
    create_upload,
    current_offset,
    finalize_upload,
    load_metadata,
)
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


def _storage_root() -> Path:
    return Path(settings.NAKAVID_STORAGE_ROOT)


def _tus_response(*, status: int = 204, upload_offset: int | None = None) -> HttpResponse:
    response = HttpResponse(status=status)
    response["Tus-Resumable"] = TUS_RESUMABLE_HEADER
    response["Tus-Version"] = TUS_VERSION
    if upload_offset is not None:
        response["Upload-Offset"] = str(upload_offset)
    return response


def _tus_error(message: str, *, status_code: int = 400) -> JsonResponse:
    return JsonResponse({"error": message}, status=status_code)


def _require_tus_resumable(request) -> HttpResponse | None:
    if request.headers.get("Tus-Resumable") != TUS_RESUMABLE_HEADER:
        return _tus_error("Missing or unsupported Tus-Resumable header.", status_code=412)
    return None


@login_required
def type_a_ingest(request):
    return render(request, "library/type_a_ingest.html")


@login_required
@require_http_methods(["POST", "OPTIONS"])
def type_a_upload_create(request):
    if request.method == "OPTIONS":
        response = HttpResponse(status=204)
        response["Tus-Resumable"] = TUS_RESUMABLE_HEADER
        response["Tus-Version"] = TUS_VERSION
        response["Tus-Extension"] = "creation"
        return response

    unsupported = _require_tus_resumable(request)
    if unsupported is not None:
        return unsupported

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return _tus_error("Request body must be JSON.")

    form = TypeAIngestMetadataForm(payload)
    if not form.is_valid():
        return JsonResponse({"errors": form.errors}, status=400)

    metadata = create_upload(
        storage_root=_storage_root(),
        user_id=request.user.id,
        class_name=form.cleaned_data["class_name"],
        theme=form.cleaned_data["theme"],
        recorded_at=form.cleaned_data["recorded_at"],
        filename=form.cleaned_data["filename"],
        upload_length=form.cleaned_data["upload_length"],
    )
    upload_url = request.build_absolute_uri(
        reverse("type-a-upload-detail", kwargs={"upload_id": metadata.upload_id})
    )
    response = HttpResponse(status=201)
    response["Location"] = upload_url
    response["Tus-Resumable"] = TUS_RESUMABLE_HEADER
    response["Tus-Version"] = TUS_VERSION
    return response


@login_required
@require_http_methods(["HEAD", "PATCH", "OPTIONS"])
def type_a_upload_detail(request, upload_id: str):
    if request.method == "OPTIONS":
        response = HttpResponse(status=204)
        response["Tus-Resumable"] = TUS_RESUMABLE_HEADER
        response["Tus-Version"] = TUS_VERSION
        return response

    unsupported = _require_tus_resumable(request)
    if unsupported is not None:
        return unsupported

    storage_root = _storage_root()
    try:
        if request.method == "HEAD":
            offset = current_offset(storage_root, upload_id)
            return _tus_response(status=200, upload_offset=offset)

        offset_header = request.headers.get("Upload-Offset")
        if offset_header is None:
            return _tus_error("Upload-Offset header is required.")
        offset = int(offset_header)
        new_offset = append_chunk(
            storage_root=storage_root,
            upload_id=upload_id,
            user_id=request.user.id,
            offset=offset,
            chunk=request.body,
        )
        upload_metadata = load_metadata(storage_root, upload_id)
        if new_offset < upload_metadata.upload_length:
            return _tus_response(upload_offset=new_offset)

        destination, source_path = finalize_upload(
            storage_root=storage_root,
            upload_id=upload_id,
            user_id=request.user.id,
        )
        duration_seconds = probe_duration_seconds(destination)
        title = Path(destination.name).stem
        recorded_at = timezone.make_aware(datetime.combine(upload_metadata.recorded_on, time.min))
        with transaction.atomic():
            Video.objects.create(
                title=title,
                source_path=source_path,
                video_type=Video.VideoType.TYPE_A,
                orientation=Video.Orientation.LANDSCAPE,
                class_name=upload_metadata.class_name,
                theme=upload_metadata.theme,
                recorded_at=recorded_at,
                duration_seconds=duration_seconds,
                is_private=True,
                created_by=request.user,
            )
        return _tus_response(status=201, upload_offset=new_offset)
    except ResumableUploadError as exc:
        return _tus_error(str(exc), status_code=exc.status_code)
    except ValueError:
        return _tus_error("Upload-Offset must be an integer.")


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


def _filtered_source_videos(*, form: SourceVideosFilterForm):
    videos = Video.objects.filter(video_type=Video.VideoType.TYPE_A).order_by(
        "-recorded_at",
        "-id",
    )
    class_name = form.cleaned_data.get("class_name")
    recorded_date = form.cleaned_data.get("recorded_date")

    if class_name:
        videos = videos.filter(class_name__iexact=class_name)
    if recorded_date:
        videos = videos.filter(recorded_at__date=recorded_date)
    return videos


@login_required
def source_videos(request):
    form = SourceVideosFilterForm(request.GET)
    videos = _filtered_source_videos(form=form) if form.is_valid() else Video.objects.none()
    video_rows = [
        {
            "video": video,
            "duration_label": format_duration_seconds(video.duration_seconds),
        }
        for video in videos
    ]

    return render(
        request,
        "library/source_videos.html",
        {
            "form": form,
            "video_rows": video_rows,
        },
    )


@login_required
def video_stream(request, video_id: int):
    video = get_object_or_404(
        Video.objects.filter(video_type=Video.VideoType.TYPE_A),
        pk=video_id,
    )
    response = HttpResponse()
    response["X-Accel-Redirect"] = to_accel_redirect_path(video.source_path)
    response["Content-Type"] = ""
    return response


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
