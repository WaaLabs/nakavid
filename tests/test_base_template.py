"""Concerns that live in base.html and apply to every page, not one view."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

User = get_user_model()


@pytest.fixture
def coach_user(db):
    return User.objects.create_user(username="coach", password="secret123!")


@pytest.fixture
def authenticated_client(client, coach_user):
    assert client.login(username="coach", password="secret123!")
    return client, coach_user


@pytest.mark.django_db
def test_csrf_comment_does_not_leak_into_the_page(authenticated_client):
    """Django's {# #} comment syntax is single-line only — a multi-line one
    isn't stripped and leaks as literal text. Regression guard for exactly
    that landing in production once already."""
    client, _user = authenticated_client

    response = client.get(reverse("clips-browser"))

    content = response.content.decode()
    assert "Renders nothing visible" not in content
    assert "guarantees the csrftoken cookie" not in content
    assert "{#" not in content


@pytest.mark.django_db
def test_csrf_cookie_is_set_on_every_page(authenticated_client):
    client, _user = authenticated_client

    response = client.get(reverse("clips-browser"))

    assert "csrftoken" in response.cookies


@pytest.mark.django_db
def test_favicon_link_renders(authenticated_client):
    client, _user = authenticated_client

    response = client.get(reverse("clips-browser"))

    content = response.content.decode()
    assert 'rel="icon"' in content
    assert "favicon.svg" in content
