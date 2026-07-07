from django.contrib import admin
from django.http import JsonResponse
from django.urls import path


def healthcheck(_request):
    return JsonResponse({"ok": True})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz/", healthcheck, name="healthcheck"),
]
