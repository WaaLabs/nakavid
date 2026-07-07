import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

User = get_user_model()


@pytest.mark.django_db
def test_login_page_renders(client):
    response = client.get(reverse("login"))
    assert response.status_code == 200


@pytest.mark.django_db
def test_session_endpoint_requires_login(client):
    response = client.get("/session/")
    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")


@pytest.mark.django_db
def test_session_endpoint_returns_logged_in_user(client):
    user = User.objects.create_user(username="coach", password="secret123!")
    assert client.login(username="coach", password="secret123!")

    response = client.get("/session/")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "username": user.username}
