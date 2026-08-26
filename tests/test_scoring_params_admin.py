import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.pipeline.models import ScoringParams
from apps.pipeline.scoring import get_active_scoring_params

User = get_user_model()

CHANGELIST_URL = "admin:pipeline_scoringparams_changelist"
ADD_URL = "admin:pipeline_scoringparams_add"


@pytest.fixture
def admin_client(client):
    User.objects.create_superuser(username="founder", password="secret123!")
    assert client.login(username="founder", password="secret123!")
    return client


@pytest.mark.django_db
def test_scoring_params_changelist_renders(admin_client):
    response = admin_client.get(reverse(CHANGELIST_URL))

    assert response.status_code == 200


@pytest.mark.django_db
def test_changelist_flags_only_the_active_row(admin_client):
    seeded = ScoringParams.objects.get()
    newer = ScoringParams.objects.create()

    response = admin_client.get(reverse(CHANGELIST_URL))
    rows = {obj.pk: obj.active_flag for obj in response.context["cl"].result_list}

    assert rows[newer.pk] is True
    assert rows[seeded.pk] is False
    assert get_active_scoring_params().pk == newer.pk


@pytest.mark.django_db
def test_add_form_prefills_from_the_active_row(admin_client):
    active = ScoringParams.objects.get()
    active.peak_count = 12
    active.window_size_seconds = 7
    active.save()

    response = admin_client.get(reverse(ADD_URL))
    initial = response.context["adminform"].form.initial

    assert initial["peak_count"] == 12
    assert initial["window_size_seconds"] == 7
    assert "id" not in initial


@pytest.mark.django_db
def test_saving_the_add_form_creates_a_new_active_set(admin_client):
    original = ScoringParams.objects.get()
    payload = {
        field.name: getattr(original, field.name)
        for field in ScoringParams._meta.fields
        if field.name not in {"id", "created_at", "updated_at"}
    }
    payload["peak_count"] = 20

    response = admin_client.post(reverse(ADD_URL), payload)

    assert response.status_code == 302
    assert ScoringParams.objects.count() == 2
    active = get_active_scoring_params()
    assert active.pk != original.pk
    assert active.peak_count == 20
