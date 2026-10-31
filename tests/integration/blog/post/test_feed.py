"""``GET /blog/post/feed``: what the RSS route renders, in one response."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from blog.factories.post import BlogPostFactory
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

FEED_KEYS = {
    "id",
    "uuid",
    "slug",
    "translations",
    "category",
    "mainImagePath",
    "publishedAt",
    "createdAt",
    "updatedAt",
}


@pytest.fixture
def client():
    return APIClient()


def _get(client, **query):
    return client.get(reverse("blog-post-feed"), query)


def test_each_item_carries_the_whole_article_and_nothing_a_card_adds(client):
    BlogPostFactory(slug="feed-article")

    response = _get(client)

    assert response.status_code == status.HTTP_200_OK
    item = next(i for i in response.json() if i["slug"] == "feed-article")
    assert set(item) == FEED_KEYS
    for fields in item["translations"].values():
        assert set(fields) == {"title", "subtitle", "body"}
        assert fields["body"]


def test_newest_first_published_only(client):
    now = timezone.now()
    old = BlogPostFactory(slug="feed-old", published_at=now - timedelta(days=2))
    new = BlogPostFactory(slug="feed-new", published_at=now - timedelta(days=1))
    BlogPostFactory(slug="feed-draft", is_published=False)
    BlogPostFactory(slug="feed-scheduled", published_at=now + timedelta(days=3))

    ids = [i["id"] for i in _get(client).json()]

    assert ids.index(new.id) < ids.index(old.id)
    slugs = {i["slug"] for i in _get(client).json()}
    assert not slugs & {"feed-draft", "feed-scheduled"}


@override_settings(BLOG_FEED_LIMIT=2)
def test_limit_is_capped_by_the_store(client):
    for n in range(4):
        BlogPostFactory(slug=f"feed-cap-{n}")

    assert len(_get(client).json()) == 2
    assert len(_get(client, limit=1).json()) == 1
    assert len(_get(client, limit=99).json()) == 2
    assert len(_get(client, limit="nonsense").json()) == 2
    assert len(_get(client, limit=0).json()) == 1


def test_staff_do_not_see_drafts_in_the_feed(client):
    BlogPostFactory(slug="feed-staff-draft", is_published=False)
    client.force_authenticate(
        UserAccountFactory(is_staff=True, is_superuser=True)
    )

    slugs = {i["slug"] for i in _get(client).json()}

    assert "feed-staff-draft" not in slugs
