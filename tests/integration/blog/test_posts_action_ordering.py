"""``?ordering=`` on the author and category post listings.

Measured on production 2026-09-18: ``/blog/author/4/posts?ordering=
createdAt`` and ``-createdAt`` returned the identical page, because the
actions blanked ``ordering_fields`` to keep the parent viewset's columns
off a BlogPost queryset — and the storefront's sort control on those
pages changed nothing. The schema mirrored the blanking's cause: it
advertised the AUTHOR's columns on the posts action, so the storefront's
generated query schema forwarded keys the API would never honour.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from drf_spectacular.generators import SchemaGenerator
from rest_framework.test import APIClient

from blog.factories import (
    BlogAuthorFactory,
    BlogCategoryFactory,
    BlogPostFactory,
)
from blog.models.post import BlogPost
from user.factories import UserAccountFactory


def _ids(response) -> list[int]:
    assert response.status_code == 200, response.content
    return [row["id"] for row in response.json()["results"]]


def _backdate(post: BlogPost, days: int) -> None:
    BlogPost.objects.filter(pk=post.pk).update(
        created_at=timezone.now() - timedelta(days=days)
    )


@pytest.fixture
def client() -> APIClient:
    return APIClient()


@pytest.mark.django_db
class TestAuthorPosts:
    def test_ascending_and_descending_differ(self, client):
        author = BlogAuthorFactory(user=UserAccountFactory())
        older = BlogPostFactory(author=author, is_published=True)
        newer = BlogPostFactory(author=author, is_published=True)
        _backdate(older, 3)

        url = reverse("blog-author-posts", args=[author.pk])

        assert _ids(client.get(url, {"ordering": "createdAt"})) == [
            older.pk,
            newer.pk,
        ]
        assert _ids(client.get(url, {"ordering": "-createdAt"})) == [
            newer.pk,
            older.pk,
        ]
        # Default: newest first.
        assert _ids(client.get(url)) == [newer.pk, older.pk]

    def test_the_parent_viewset_columns_are_ignored_not_applied(self, client):
        # ``user__first_name`` is an AUTHOR column; on the posts queryset
        # it would be a FieldError. It is dropped and the default holds.
        author = BlogAuthorFactory(user=UserAccountFactory())
        older = BlogPostFactory(author=author, is_published=True)
        newer = BlogPostFactory(author=author, is_published=True)
        _backdate(older, 3)

        url = reverse("blog-author-posts", args=[author.pk])

        assert _ids(client.get(url, {"ordering": "user_FirstName"})) == [
            newer.pk,
            older.pk,
        ]


@pytest.mark.django_db
class TestCategoryPosts:
    def test_ascending_and_descending_differ(self, client):
        category = BlogCategoryFactory()
        older = BlogPostFactory(category=category, is_published=True)
        newer = BlogPostFactory(category=category, is_published=True)
        _backdate(older, 3)

        url = reverse("blog-category-posts", args=[category.pk])

        assert _ids(client.get(url, {"ordering": "createdAt"})) == [
            older.pk,
            newer.pk,
        ]
        assert _ids(client.get(url, {"ordering": "-createdAt"})) == [
            newer.pk,
            older.pk,
        ]


@pytest.fixture(scope="module")
def paths():
    return SchemaGenerator().get_schema(request=None, public=True)["paths"]


class TestSchema:
    """The contract the storefront generates its query schema from."""

    @staticmethod
    def _ordering_param(paths, path: str):
        params = paths[path]["get"].get("parameters", [])
        return next((p for p in params if p["name"] == "ordering"), None)

    def test_author_posts_advertise_the_post_columns(self, paths):
        param = self._ordering_param(paths, "/api/v1/blog/author/{id}/posts")

        assert param is not None
        pattern = param["schema"]["pattern"]
        assert "publishedAt" in pattern
        assert "viewCount" in pattern
        # The author's own columns no longer leak onto the posts action.
        assert "user_FirstName" not in pattern
        assert "website" not in pattern

    def test_category_posts_advertise_the_post_columns(self, paths):
        param = self._ordering_param(paths, "/api/v1/blog/category/{id}/posts")

        assert param is not None
        assert "publishedAt" in param["schema"]["pattern"]
        assert "treeId" not in param["schema"]["pattern"]

    def test_an_action_that_sorts_nothing_advertises_no_ordering(self, paths):
        # ``data_exports`` orders its queryset by hand and never runs the
        # backend; the base OrderingFilter used to advertise the USER's
        # columns on it regardless.
        assert (
            self._ordering_param(
                paths, "/api/v1/user/account/{id}/data_exports"
            )
            is None
        )
