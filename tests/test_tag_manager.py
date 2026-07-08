from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.library.models import Tag, TagCategory

User = get_user_model()


@pytest.fixture
def coach_user(db):
    return User.objects.create_user(username="coach", password="secret123!")


@pytest.fixture
def authenticated_client(client, coach_user):
    assert client.login(username="coach", password="secret123!")
    return client, coach_user


@pytest.mark.django_db
def test_tag_uses_slug_label_and_category_lookup():
    category = TagCategory.objects.create(name="Activity", description="Lesson activities")
    tag = Tag.objects.create(slug="warmup", label="Warm-up", category=category)

    assert tag.slug == "warmup"
    assert tag.label == "Warm-up"
    assert tag.category == category
    assert list(category.tags.values_list("slug", flat=True)) == ["warmup"]


@pytest.mark.django_db
def test_tag_manager_requires_login(client):
    response = client.get(reverse("tag-manager"))

    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")


@pytest.mark.django_db
def test_tag_manager_creates_tag_and_category(authenticated_client):
    client, _user = authenticated_client

    create_category = client.post(
        reverse("tag-manager"),
        {
            "action": "create_category",
            "category-name": "Theme",
            "category-description": "Lesson themes",
        },
    )
    assert create_category.status_code == 302
    category = TagCategory.objects.get(name="Theme")
    assert category.description == "Lesson themes"

    create_tag = client.post(
        reverse("tag-manager"),
        {
            "action": "create_tag",
            "tag-label": "Animals",
            "tag-slug": "",
            "tag-category": str(category.id),
        },
    )
    assert create_tag.status_code == 302
    tag = Tag.objects.get(label="Animals")
    assert tag.slug == "animals"
    assert tag.category_id == category.id

    listing = client.get(reverse("tag-manager"))
    assert listing.status_code == 200
    assert b"Animals" in listing.content
    assert b"animals" in listing.content
    assert b"Theme" in listing.content


@pytest.mark.django_db
def test_tag_edit_and_delete(authenticated_client):
    client, _user = authenticated_client
    category = TagCategory.objects.create(name="Skill")
    tag = Tag.objects.create(slug="listen", label="Listening", category=category)

    edit_get = client.get(reverse("tag-edit", kwargs={"tag_id": tag.id}))
    assert edit_get.status_code == 200
    assert b"Listening" in edit_get.content

    edit_post = client.post(
        reverse("tag-edit", kwargs={"tag_id": tag.id}),
        {
            "label": "Listening hard",
            "slug": "listening-hard",
            "category": str(category.id),
        },
    )
    assert edit_post.status_code == 302
    tag.refresh_from_db()
    assert tag.label == "Listening hard"
    assert tag.slug == "listening-hard"

    delete_post = client.post(reverse("tag-delete", kwargs={"tag_id": tag.id}))
    assert delete_post.status_code == 302
    assert not Tag.objects.filter(pk=tag.id).exists()


@pytest.mark.django_db
def test_tag_and_category_registered_in_admin(admin_client):
    assert admin_client.get("/admin/library/tag/").status_code == 200
    assert admin_client.get("/admin/library/tagcategory/").status_code == 200
