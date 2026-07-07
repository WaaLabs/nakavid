from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.urls import include, path

from apps.library.views import (
    clip_stream,
    clips_browser,
    type_a_ingest,
    type_a_upload_create,
    type_a_upload_detail,
    type_b_ingest,
)


def healthcheck(_request):
    return JsonResponse({"ok": True})


@login_required
def session_info(request):
    return JsonResponse({"ok": True, "username": request.user.get_username()})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("healthz/", healthcheck, name="healthcheck"),
    path("session/", session_info, name="session-info"),
    path("clips/", clips_browser, name="clips-browser"),
    path("clips/<int:clip_id>/stream/", clip_stream, name="clip-stream"),
    path("ingest/type-b/", type_b_ingest, name="type-b-ingest"),
    path("ingest/type-a/", type_a_ingest, name="type-a-ingest"),
    path("ingest/type-a/uploads/", type_a_upload_create, name="type-a-upload-create"),
    path(
        "ingest/type-a/uploads/<str:upload_id>/",
        type_a_upload_detail,
        name="type-a-upload-detail",
    ),
]
