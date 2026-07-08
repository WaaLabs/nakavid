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
from django.views.decorators.http import require_http_methods, require_POST

from apps.library.duration import format_duration_seconds
from apps.library.forms import (
    BulkTagForm,
    ClipsBrowserFilterForm,
    CombineBuilderSubmitForm,
    SourceVideosFilterForm,
    TagCategoryForm,
    TagForm,
    TypeAIngestMetadataForm,
    TypeBIngestForm,
)
from apps.library.models import Clip, Combine, CombineClip, Tag, TagCategory, Video
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
from apps.pipeline.enqueue import (
    STUB_DURATION_SECONDS,
    enqueue_combine_export_job,
    enqueue_probe_job,
)
from apps.pipeline.models import Job


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
        duration_seconds = STUB_DURATION_SECONDS
        title = Path(destination.name).stem
        recorded_at = timezone.make_aware(datetime.combine(upload_metadata.recorded_on, time.min))
        with transaction.atomic():
            video = Video.objects.create(
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
            enqueue_probe_job(video=video)
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
            duration_seconds = STUB_DURATION_SECONDS
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
                enqueue_probe_job(video=video)

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


def _lesson_view_clip_payload(clips: list[Clip]) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    for clip in clips:
        start = float(clip.start_seconds)
        end = float(clip.end_seconds)
        payload.append(
            {
                "id": clip.id,
                "startSeconds": start,
                "endSeconds": end,
                "highlightScore": clip.highlight_score,
                "label": f"{start:.1f}s–{end:.1f}s",
            }
        )
    return payload


@login_required
def lesson_view(request, video_id: int):
    video = get_object_or_404(
        Video.objects.filter(video_type=Video.VideoType.TYPE_A),
        pk=video_id,
    )
    clips = list(video.clips.order_by("start_seconds", "id"))
    clips_json = json.dumps(
        _lesson_view_clip_payload(clips),
        separators=(",", ":"),
    )
    return render(
        request,
        "library/lesson_view.html",
        {
            "video": video,
            "clips": clips,
            "clips_json": clips_json,
            "duration_label": format_duration_seconds(video.duration_seconds),
            "player_selector": "#lesson-source-player",
        },
    )


@login_required
def queue_status(request):
    jobs = list(
        Job.objects.select_related("video", "scoring_params").order_by("-created_at", "-id")[:50]
    )
    status_counts = {
        status: sum(1 for job in jobs if job.status == status)
        for status, _label in Job.Status.choices
    }
    return render(
        request,
        "library/queue_status.html",
        {
            "jobs": jobs,
            "status_counts": status_counts,
        },
    )


@login_required
@require_POST
def queue_requeue_job(request, job_id: int):
    job = get_object_or_404(Job, pk=job_id)
    if job.status == Job.Status.ERROR:
        job.status = Job.Status.PENDING
        job.claimed_at = None
        job.finished_at = None
        job.stderr = ""
        job.save(update_fields=["status", "claimed_at", "finished_at", "stderr", "updated_at"])
    return redirect("queue-status")


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


@login_required
@require_http_methods(["GET", "POST"])
def tag_manager(request):
    tag_form = TagForm(prefix="tag")
    category_form = TagCategoryForm(prefix="category")

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create_tag":
            tag_form = TagForm(request.POST, prefix="tag")
            if tag_form.is_valid():
                tag = tag_form.save()
                messages.success(request, f"Created tag “{tag.label}”.")
                return redirect("tag-manager")
        elif action == "create_category":
            category_form = TagCategoryForm(request.POST, prefix="category")
            if category_form.is_valid():
                category = category_form.save()
                messages.success(request, f"Created tag category “{category.name}”.")
                return redirect("tag-manager")

    tags = Tag.objects.select_related("category").order_by("label", "slug")
    categories = TagCategory.objects.order_by("name")
    return render(
        request,
        "library/tag_manager.html",
        {
            "tag_form": tag_form,
            "category_form": category_form,
            "tags": tags,
            "categories": categories,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def tag_edit(request, tag_id: int):
    tag = get_object_or_404(Tag.objects.select_related("category"), pk=tag_id)
    if request.method == "POST":
        form = TagForm(request.POST, instance=tag)
        if form.is_valid():
            updated = form.save()
            messages.success(request, f"Updated tag “{updated.label}”.")
            return redirect("tag-manager")
    else:
        form = TagForm(instance=tag)

    return render(
        request,
        "library/tag_edit.html",
        {
            "form": form,
            "tag": tag,
        },
    )


@login_required
@require_POST
def tag_delete(request, tag_id: int):
    tag = get_object_or_404(Tag, pk=tag_id)
    label = tag.label
    tag.delete()
    messages.success(request, f"Deleted tag “{label}”.")
    return redirect("tag-manager")


def _bulk_tag_summary(*, video_count: int, clip_count: int, tag_count: int, action: str) -> str:
    targets: list[str] = []
    if video_count:
        targets.append(f"{video_count} video{'s' if video_count != 1 else ''}")
    if clip_count:
        targets.append(f"{clip_count} clip{'s' if clip_count != 1 else ''}")
    target_label = " and ".join(targets)
    verb = "Added" if action == BulkTagForm.Action.ADD else "Removed"
    return f"{verb} {tag_count} tag{'s' if tag_count != 1 else ''} on {target_label}."


@login_required
@require_http_methods(["GET", "POST"])
def bulk_tagging(request):
    if request.method == "POST":
        form = BulkTagForm(request.POST)
        if form.is_valid():
            videos = list(form.cleaned_data["videos"])
            clips = list(form.cleaned_data["clips"])
            tags = list(form.cleaned_data["tags"])
            action = form.cleaned_data["action"]
            with transaction.atomic():
                for video in videos:
                    if action == BulkTagForm.Action.ADD:
                        video.tags.add(*tags)
                    else:
                        video.tags.remove(*tags)
                for clip in clips:
                    if action == BulkTagForm.Action.ADD:
                        clip.tags.add(*tags)
                    else:
                        clip.tags.remove(*tags)
            messages.success(
                request,
                _bulk_tag_summary(
                    video_count=len(videos),
                    clip_count=len(clips),
                    tag_count=len(tags),
                    action=action,
                ),
            )
            return redirect("bulk-tagging")
    else:
        form = BulkTagForm()

    return render(request, "library/bulk_tagging.html", {"form": form})


def _combine_builder_clip_payload(*, clips: list[Clip], request) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    for clip in clips:
        start = float(clip.start_seconds)
        end = float(clip.end_seconds)
        label = f"{clip.video.title} · {start:.1f}s–{end:.1f}s · score {clip.highlight_score}"
        payload.append(
            {
                "id": clip.id,
                "label": label,
                "streamUrl": request.build_absolute_uri(
                    reverse("clip-stream", kwargs={"clip_id": clip.id})
                ),
                "durationSeconds": max(end - start, 0.0),
                "highlightScore": clip.highlight_score,
            }
        )
    return payload


@login_required
def combine_builder(request):
    form = ClipsBrowserFilterForm(request.GET)
    clips = _filtered_clips(form=form) if form.is_valid() else Clip.objects.none()
    clip_list = list(clips)
    clips_json = json.dumps(
        _combine_builder_clip_payload(clips=clip_list, request=request),
        separators=(",", ":"),
    )
    return render(
        request,
        "library/combine_builder.html",
        {
            "form": form,
            "clips_json": clips_json,
            "submit_url": reverse("combine-builder-submit"),
        },
    )


@login_required
@require_POST
def combine_builder_submit(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"errors": {"__all__": ["Request body must be JSON."]}}, status=400)

    form = CombineBuilderSubmitForm(payload)
    if not form.is_valid():
        return JsonResponse({"errors": form.errors}, status=400)

    clip_ids = form.cleaned_data["clip_ids"]
    clips_by_id = {
        clip.pk: clip for clip in Clip.objects.select_related("video").filter(pk__in=clip_ids)
    }

    with transaction.atomic():
        combine = Combine.objects.create(
            title=form.cleaned_data["title"],
            created_by=request.user,
        )
        for position, clip_id in enumerate(clip_ids, start=1):
            CombineClip.objects.create(
                combine=combine,
                clip=clips_by_id[clip_id],
                position=position,
            )
        job = enqueue_combine_export_job(combine=combine)

    return JsonResponse(
        {
            "combineId": combine.pk,
            "jobId": job.pk,
            "queueStatusUrl": request.build_absolute_uri(reverse("queue-status")),
        },
        status=201,
    )
