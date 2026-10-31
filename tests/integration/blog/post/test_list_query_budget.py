"""A list of post cards costs the same queries however long it is.

A card reads the post's three counts, its translations, tags, author and
category. Each list below used to hand-build its queryset, and once the
likers stopped being prefetched (the card no longer lists them) every
post cost a query for ``likes_count`` as well as the ones for its
comments and tags.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from blog.factories.author import BlogAuthorFactory
from blog.factories.category import BlogCategoryFactory
from blog.factories.comment import BlogCommentFactory
from blog.factories.post import BlogPostFactory
from blog.factories.tag import BlogTagFactory
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


def _posts(count, *, category, author):
    for _ in range(count):
        post = BlogPostFactory(category=category, author=author)
        post.tags.add(BlogTagFactory(active=True), BlogTagFactory(active=True))
        post.likes.add(UserAccountFactory(), UserAccountFactory())
        BlogCommentFactory(post=post, approved=True)


def _queries(url):
    client = APIClient()
    client.get(url)  # warm per-process caches (tenant, settings, content types)
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url)
    assert response.status_code == status.HTTP_200_OK
    return len(queries)


@pytest.fixture
def world():
    category = BlogCategoryFactory()
    author = BlogAuthorFactory(user=UserAccountFactory())
    anchor = BlogPostFactory(category=category, author=author)
    return category, author, anchor


@pytest.mark.parametrize(
    "route",
    ["category_posts", "author_posts", "related_posts", "list"],
)
def test_the_query_count_does_not_grow_with_the_posts(world, route):
    category, author, anchor = world
    url = {
        "category_posts": reverse("blog-category-posts", args=[category.pk]),
        "author_posts": reverse("blog-author-posts", args=[author.pk]),
        "related_posts": reverse("blog-post-related_posts", args=[anchor.pk]),
        "list": reverse("blog-post-list"),
    }[route]

    _posts(2, category=category, author=author)
    few = _queries(url)
    _posts(5, category=category, author=author)
    many = _queries(url)

    assert many == few


def test_the_counts_are_subqueries_not_joined_aggregates():
    """Likes x comments x tags rows must not multiply before grouping."""
    from blog.models.post import BlogPost

    sql = str(BlogPost.objects.for_list().query)

    assert "GROUP BY" not in sql
    assert sql.count("COUNT(") == 3


def test_the_counts_count_what_the_property_would():
    from blog.models.post import BlogPost

    category = BlogCategoryFactory()
    author = BlogAuthorFactory(user=UserAccountFactory())
    popular = BlogPostFactory(category=category, author=author)
    popular.tags.add(
        *[BlogTagFactory(active=True) for _ in range(3)],
        BlogTagFactory(active=False),
    )
    popular.likes.add(*[UserAccountFactory() for _ in range(4)])
    for _ in range(3):
        BlogCommentFactory(post=popular, approved=True)
    BlogCommentFactory(post=popular, approved=False)
    quiet = BlogPostFactory(category=category, author=author)

    counted = {
        post.pk: (post.likes_count, post.comments_count, post.tags_count)
        for post in BlogPost.objects.for_list()
    }

    assert counted[popular.pk] == (4, 3, 3)
    assert counted[quiet.pk] == (0, 0, 0)
    for post in (popular, quiet):
        fresh = BlogPost.objects.get(pk=post.pk)
        assert counted[post.pk] == (
            fresh.likes_count,
            fresh.comments_count,
            fresh.tags_count,
        )
    assert (
        BlogPost.objects.with_comments_count(approved_only=False)
        .get(pk=popular.pk)
        .comments_count
        == 4
    )
    assert (
        BlogPost.objects.with_tags_count(active_only=False)
        .get(pk=popular.pk)
        .tags_count
        == 4
    )
