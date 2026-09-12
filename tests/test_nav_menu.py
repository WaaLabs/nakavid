"""The nav's Upload/Settings menus are native <details>, which never close on
their own when you click elsewhere on the page — nav.js is what closes them.
Nothing here can run that JS; it just guards the wiring a Python test can see.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

User = get_user_model()


@pytest.fixture
def authenticated_client(client, db):
    User.objects.create_user(username="coach", password="secret123!")
    assert client.login(username="coach", password="secret123!")
    return client


@pytest.mark.django_db
def test_every_page_loads_the_close_on_outside_click_script(authenticated_client):
    response = authenticated_client.get(reverse("clips-browser"))

    assert b'src="/static/js/nav.js"' in response.content


@pytest.mark.django_db
def test_the_nav_has_two_disclosure_menus(authenticated_client):
    response = authenticated_client.get(reverse("clips-browser"))

    assert response.content.count(b'<details class="nv-menu">') == 2
