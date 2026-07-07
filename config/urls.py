from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.urls import include, path


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
]
