"""A draft post must not leak through anything that references it.

`GET /api/v1/blog/post/<draft>` correctly answers 404. Two other routes
reached the same row and returned its body in full:

* `BlogAuthorDetailSerializer.get_recent_posts` / `get_top_posts` listed
  `obj.blog_posts` — the raw reverse accessor, which bypasses
  `BlogPostQuerySet.visible_to` entirely. The manager's own docstring
  warns about exactly this. And `BlogPostDetailSerializer.get_author`
  embeds that serializer, so every PUBLISHED post leaked its author's
  drafts too.

* `GET /api/v1/blog/comment/<pk>/post` serialized `comment.post` with no
  gate, and `?post__isPublished=false` on the anonymous comment list was
  the handle for finding those comments. Measured: 6541 characters of a
  draft's body.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from blog.factories.author import BlogAuthorFactory
from blog.factories.comment import BlogCommentFactory
from blog.factories.post import BlogPostFactory
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def author_with_a_draft():
    author = BlogAuthorFactory()
    live = BlogPostFactory(author=author, is_published=True)
    draft = BlogPostFactory(author=author, is_published=False)
    return author, live, draft


def test_the_direct_route_still_refuses_a_draft(author_with_a_draft):
    """The control: this one was always right."""
    _author, _live, draft = author_with_a_draft

    response = APIClient().get(
        reverse("blog-post-detail", kwargs={"pk": draft.pk})
    )

    assert response.status_code == 404


def test_an_author_page_does_not_list_their_drafts(author_with_a_draft):
    author, live, draft = author_with_a_draft

    response = APIClient().get(
        reverse("blog-author-detail", kwargs={"pk": author.pk})
    )

    assert response.status_code == 200
    listed = {
        row["id"]
        for key in ("recent_posts", "top_posts")
        for row in response.data.get(key) or []
    }
    assert draft.pk not in listed, "the author page published a draft"
    assert live.pk in listed, "and it must still list the live one"


def test_a_comment_cannot_be_used_to_read_its_draft(author_with_a_draft):
    _author, _live, draft = author_with_a_draft
    comment = BlogCommentFactory(
        post=draft, user=UserAccountFactory(), approved=True
    )

    anon = APIClient()

    assert (
        anon.get(
            reverse("blog-comment-post", kwargs={"pk": comment.pk})
        ).status_code
        == 404
    )
    assert (
        anon.get(
            reverse("blog-comment-detail", kwargs={"pk": comment.pk})
        ).status_code
        == 404
    )


def test_the_unpublished_filter_is_not_an_enumeration_handle(
    author_with_a_draft,
):
    _author, _live, draft = author_with_a_draft
    hidden = BlogCommentFactory(
        post=draft, user=UserAccountFactory(), approved=True
    )

    response = APIClient().get(
        reverse("blog-comment-list"), {"post__isPublished": "false"}
    )

    assert response.status_code == 200
    returned = {row["id"] for row in response.data["results"]}
    assert hidden.pk not in returned


def test_a_comment_on_a_published_post_is_still_readable(author_with_a_draft):
    """The fix must not hide ordinary comments."""
    _author, live, _draft = author_with_a_draft
    comment = BlogCommentFactory(
        post=live, user=UserAccountFactory(), approved=True
    )

    anon = APIClient()

    assert (
        anon.get(
            reverse("blog-comment-post", kwargs={"pk": comment.pk})
        ).status_code
        == 200
    )
    assert (
        anon.get(
            reverse("blog-comment-detail", kwargs={"pk": comment.pk})
        ).status_code
        == 200
    )
