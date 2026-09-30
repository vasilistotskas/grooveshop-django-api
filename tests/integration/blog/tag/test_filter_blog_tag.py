"""``BlogTagFilter`` (blog/filters/tag.py) through ``/api/v1/blog/tag``.

Every case asserts the exact set of tags returned from one fixed
dataset, so an undeclared parameter — which django-filter ignores
silently — fails instead of passing on the unfiltered list.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from blog.factories.post import BlogPostFactory
from blog.factories.tag import BlogTagFactory
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

URL = reverse("blog-tag-list")


def _tag(name, *, active=True, sort_order):
    tag = BlogTagFactory(active=active, sort_order=sort_order, translations=[])
    tag.set_current_language("el")
    tag.name = name
    tag.save()
    return tag


@pytest.fixture
def tags():
    """python: 2 published posts, liked by 2 people.
    django: 1 published + 1 draft post, liked by 1 person; the draft counts
    nowhere, as the public never sees it.
    archive: unused. hidden: inactive, so never listed publicly
    (``BlogTagManager.for_list``), however it is filtered."""
    first, second = UserAccountFactory.create_batch(2, num_addresses=0)
    python = _tag("Python", sort_order=1)
    django = _tag("Django", sort_order=2)
    archive = _tag("Archive", sort_order=3)
    hidden = _tag("Hidden", active=False, sort_order=4)

    liked_by_both = BlogPostFactory(image=None)
    liked_by_both.tags.add(python)
    liked_by_both.likes.add(first, second)

    shared = BlogPostFactory(image=None)
    shared.tags.add(python, django)
    shared.likes.add(first)

    draft = BlogPostFactory(image=None, is_published=False)
    draft.tags.add(django, hidden)

    return {
        "python": python,
        "django": django,
        "archive": archive,
        "draft": draft,
    }


def _names(params) -> list[str]:
    response = APIClient().get(URL, params)
    assert response.status_code == 200, response.data
    return [
        tag["translations"]["el"]["name"] for tag in response.data["results"]
    ]


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"active": "true"}, {"Python", "Django", "Archive"}),
        ({"active": "false"}, set()),
        ({"name": "yth"}, {"Python"}),
        ({"name__startswith": "dj"}, {"Django"}),
        ({"name__exact": "Django"}, {"Django"}),
        ({"has_name": "true"}, {"Python", "Django", "Archive"}),
        ({"has_posts": "true"}, {"Python", "Django"}),
        ({"has_posts": "false"}, {"Archive"}),
        ({"min_posts": 2}, {"Python"}),
        ({"max_posts": 0}, {"Archive"}),
        ({"unused": "true"}, {"Archive"}),
        ({"post__is_published": "false"}, {"Django"}),
        ({"has_liked_posts": "true"}, {"Python", "Django"}),
        ({"has_liked_posts": "false"}, {"Archive"}),
        ({"min_total_likes": 2}, {"Python"}),
        # The camelCase spelling the storefront sends reaches the same
        # filters.
        ({"hasPosts": "false"}, {"Archive"}),
        ({"minTotalLikes": 2}, {"Python"}),
        ({"active": "true", "max_posts": 1}, {"Django", "Archive"}),
    ],
)
def test_each_filter_selects_exactly_its_tags(tags, params, expected):
    assert set(_names(params)) == expected


def test_a_post_filter_selects_the_tags_on_that_post(tags):
    assert set(_names({"post": tags["draft"].id})) == {"Django"}


def test_uuid_selects_one_tag(tags):
    assert _names({"uuid": str(tags["archive"].uuid)}) == ["Archive"]


def test_ranking_by_likes_counts_every_like_not_every_liker(tags):
    """Python's posts carry 3 likes from 2 people; Django's carry 1."""
    assert _names({"ordering": "-totalLikes"}) == [
        "Python",
        "Django",
        "Archive",
    ]


def test_ranking_by_posts_puts_unused_tags_last(tags):
    assert _names({"ordering": "-postsCount"})[-1] == "Archive"


def test_the_listed_counts_are_the_published_ones(tags):
    response = APIClient().get(URL, {"ordering": "sortOrder"})

    assert response.status_code == 200, response.data
    counts = {
        tag["translations"]["el"]["name"]: tag["posts_count"]
        for tag in response.data["results"]
    }
    assert counts == {"Python": "2", "Django": "1", "Archive": "0"}


def test_listing_costs_the_same_queries_for_one_tag_or_many(
    tags, django_assert_num_queries
):
    """The counts come from the list annotation, not a query per tag."""
    client = APIClient()
    with CaptureQueriesContext(connection) as three_tags:
        client.get(URL)
    for sort_order in range(10, 20):
        _tag(f"Extra {sort_order}", sort_order=sort_order).blog_posts.add(
            tags["draft"]
        )

    with django_assert_num_queries(len(three_tags)):
        client.get(URL)
