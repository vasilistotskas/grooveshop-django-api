"""Two anonymous 500s on the blog API.

`tagName` filtered `tags__translations__label`, but `BlogTagTranslation`
has `name` — `label` belongs to the unrelated `tag/` app. And
`liked_posts` is `AllowAny` while `likes=user` casts the user to its pk,
so an anonymous caller with a valid body reached
`TypeError: Field 'id' expected a number`.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from blog.factories.post import BlogPostFactory
from blog.factories.tag import BlogTagFactory
from blog.models.tag import BlogTag
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


def test_the_tag_name_filter_does_not_500():
    tagged = BlogPostFactory(is_published=True)
    tag = BlogTagFactory(active=True)
    tag.set_current_language("en")
    tag.name = "seasonal"
    tag.save()
    tagged.tags.add(tag)

    response = APIClient().get(
        reverse("blog-post-list"), {"tagName": "seasonal"}
    )

    assert response.status_code == 200, response.status_code
    assert tagged.pk in {row["id"] for row in response.data["results"]}


def test_the_tag_name_filter_narrows():
    """A filter that 500s cannot be wrong; one that runs can be."""
    BlogPostFactory(is_published=True)

    response = APIClient().get(
        reverse("blog-post-list"), {"tagName": "no-such-tag"}
    )

    assert response.status_code == 200
    assert response.data["results"] == []


def test_anonymous_liked_posts_is_refused_not_a_500():
    post = BlogPostFactory(is_published=True)

    response = APIClient().post(
        reverse("blog-post-liked_posts"), {"postIds": [post.pk]}, format="json"
    )

    assert response.status_code == 401, response.status_code


def test_a_signed_in_caller_still_gets_their_likes():
    user = UserAccountFactory(num_addresses=0)
    liked = BlogPostFactory(is_published=True)
    other = BlogPostFactory(is_published=True)
    liked.likes.add(user)

    client = APIClient()
    client.force_authenticate(user=user)
    response = client.post(
        reverse("blog-post-liked_posts"),
        {"postIds": [liked.pk, other.pk]},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["post_ids"] == [liked.pk]


def test_a_deactivated_tag_can_still_be_reached_by_the_admin():
    """`BlogTagAdmin.list_editable` contains `active`, so it must be.

    The filter used to live in the DEFAULT manager's `get_queryset`,
    which meant `_default_manager` too — an inactive tag vanished from
    the admin changelist and could never be ticked back on.
    """
    tag = BlogTagFactory(active=True)
    BlogTag.objects.filter(pk=tag.pk).update(active=False)

    assert BlogTag.objects.filter(pk=tag.pk).exists()
    assert BlogTag._default_manager.filter(pk=tag.pk).exists()


def test_the_public_tag_endpoints_still_hide_it():
    tag = BlogTagFactory(active=True)
    BlogTag.objects.filter(pk=tag.pk).update(active=False)

    response = APIClient().get(
        reverse("blog-tag-detail", kwargs={"pk": tag.pk})
    )

    assert response.status_code == 404
