import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.pipeline.models import IngestSettings

User = get_user_model()

CHANGELIST_URL = "admin:pipeline_ingestsettings_changelist"
ADD_URL = "admin:pipeline_ingestsettings_add"


@pytest.fixture
def admin_client(client):
    User.objects.create_superuser(username="founder", password="secret123!")
    assert client.login(username="founder", password="secret123!")
    return client


@pytest.mark.django_db
def test_visiting_the_changelist_redirects_to_the_one_rows_change_form(admin_client):
    response = admin_client.get(reverse(CHANGELIST_URL))

    settings_row = IngestSettings.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse(
        "admin:pipeline_ingestsettings_change", args=[settings_row.pk]
    )


@pytest.mark.django_db
def test_visiting_the_changelist_creates_the_row_if_missing(admin_client):
    assert not IngestSettings.objects.exists()

    admin_client.get(reverse(CHANGELIST_URL))

    assert IngestSettings.objects.count() == 1


@pytest.mark.django_db
def test_saving_the_change_form_updates_the_cutoff(admin_client):
    settings_row = IngestSettings.load()

    response = admin_client.post(
        reverse("admin:pipeline_ingestsettings_change", args=[settings_row.pk]),
        {"short_recording_max_seconds": 600},
    )

    assert response.status_code == 302
    settings_row.refresh_from_db()
    assert settings_row.short_recording_max_seconds == 600


@pytest.mark.django_db
def test_a_second_row_cannot_be_added(admin_client):
    response = admin_client.get(reverse(ADD_URL))

    assert response.status_code == 403


@pytest.mark.django_db
def test_the_one_row_cannot_be_deleted(admin_client):
    settings_row = IngestSettings.load()

    response = admin_client.post(
        reverse("admin:pipeline_ingestsettings_delete", args=[settings_row.pk])
    )

    assert response.status_code == 403
    assert IngestSettings.objects.filter(pk=settings_row.pk).exists()
