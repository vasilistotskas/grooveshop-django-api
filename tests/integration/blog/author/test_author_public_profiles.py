"""An author's public profiles (``same_as``): written by staff, served on
every author read and inside a post, the storefront's Person ``sameAs``."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from unfold.contrib.forms.widgets import ArrayWidget

from blog.admin import BlogAuthorAdmin
from blog.factories.author import BlogAuthorFactory
from blog.factories.post import BlogPostFactory
from blog.models.author import PROFILE_URLS_MAX, BlogAuthor
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

PROFILES = [
    "https://www.linkedin.com/in/someone",
    "https://www.instagram.com/someone",
]


@pytest.fixture
def staff_client():
    client = APIClient()
    client.force_authenticate(
        UserAccountFactory(num_addresses=0, is_superuser=True, is_staff=True)
    )
    return client


def test_an_author_read_carries_the_profiles():
    author = BlogAuthorFactory(same_as=PROFILES)

    response = APIClient().get(reverse("blog-author-detail", args=[author.id]))

    assert response.status_code == status.HTTP_200_OK
    assert response.data["same_as"] == PROFILES


def test_an_author_without_profiles_reads_an_empty_list():
    author = BlogAuthorFactory()

    response = APIClient().get(reverse("blog-author-list"))

    row = next(r for r in response.data["results"] if r["id"] == author.id)
    assert row["same_as"] == []


def test_a_post_carries_its_author_profiles():
    post = BlogPostFactory(author=BlogAuthorFactory(same_as=PROFILES))

    response = APIClient().get(reverse("blog-post-detail", args=[post.id]))

    assert response.status_code == status.HTTP_200_OK
    assert response.data["author"]["same_as"] == PROFILES


def test_staff_write_the_profiles(staff_client):
    author = BlogAuthorFactory()

    response = staff_client.patch(
        reverse("blog-author-detail", args=[author.id]),
        data={"same_as": PROFILES},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    author.refresh_from_db()
    assert author.same_as == PROFILES


@pytest.mark.parametrize(
    "url",
    [
        "ftp://files.example.com/me",
        "javascript:alert(1)",
        "not a url",
        # Django's URLValidator takes any five-digit port; the storefront's
        # z.url() refuses one past 65535, and would answer 422 for every
        # post of the author.
        "https://www.linkedin.com:65536/in/someone",
        "https://www.linkedin.com:99999/in/someone",
    ],
)
def test_a_profile_must_be_a_web_address(staff_client, url):
    author = BlogAuthorFactory()

    response = staff_client.patch(
        reverse("blog-author-detail", args=[author.id]),
        data={"same_as": [url]},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    author.refresh_from_db()
    assert author.same_as == []


def test_the_admin_edits_one_profile_per_input(rf):
    admin = BlogAuthorAdmin(BlogAuthor, None)
    field = BlogAuthor._meta.get_field("same_as")

    formfield = admin.formfield_for_dbfield(field, rf.get("/"))

    assert isinstance(formfield.widget, ArrayWidget)


def test_a_profile_may_name_a_port(staff_client):
    author = BlogAuthorFactory()
    url = "https://profiles.example.com:8443/someone"

    response = staff_client.patch(
        reverse("blog-author-detail", args=[author.id]),
        data={"same_as": [url]},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    author.refresh_from_db()
    assert author.same_as == [url]


def test_an_author_lists_a_bounded_number_of_profiles(staff_client):
    author = BlogAuthorFactory()
    urls = [
        f"https://profiles.example.com/{i}" for i in range(PROFILE_URLS_MAX + 1)
    ]

    response = staff_client.patch(
        reverse("blog-author-detail", args=[author.id]),
        data={"same_as": urls},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    author.refresh_from_db()
    assert author.same_as == []


def test_the_model_refuses_what_the_api_refuses():
    author = BlogAuthorFactory()
    author.same_as = ["https://www.linkedin.com:65536/in/someone"]

    with pytest.raises(ValidationError):
        author.full_clean()

    author.same_as = [
        f"https://profiles.example.com/{i}" for i in range(PROFILE_URLS_MAX + 1)
    ]
    with pytest.raises(ValidationError):
        author.full_clean()
