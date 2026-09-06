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
from blog.models.comment import BlogComment
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


class TestTheCommentTreeIsGatedToo:
    """`thread`/`replies` walk MPTT, which knows nothing about posts.

    Nothing in the database ties a reply to its parent's post, and the
    write serializer's same-post check used to be skipped whenever a
    PATCH omitted `post` — `validate_parent` read the post out of
    `initial_data`, so a payload of only `{"parent": ...}` fell through
    both branches. That made the leak reachable with two ordinary API
    calls: re-parent your own public comment onto a comment on someone
    else's draft, then read the draft's comments back as your thread's
    ancestors. Verified before the fix: PATCH 200, thread 200 carrying
    the hidden comment's content, while a direct GET on it answered 404.
    """

    def _hidden_comment(self):
        draft = BlogPostFactory(is_published=False)
        return BlogCommentFactory(
            post=draft,
            user=UserAccountFactory(num_addresses=0),
            approved=True,
        )

    def _my_comment(self, user):
        return BlogCommentFactory(
            post=BlogPostFactory(is_published=True),
            user=user,
            approved=True,
        )

    def test_a_comment_cannot_be_reparented_onto_a_hidden_one(self):
        hidden = self._hidden_comment()
        attacker = UserAccountFactory(num_addresses=0)
        mine = self._my_comment(attacker)

        client = APIClient()
        client.force_authenticate(user=attacker)
        response = client.patch(
            reverse("blog-comment-detail", kwargs={"pk": mine.pk}),
            {"parent": hidden.pk},
            format="json",
        )

        assert response.status_code == 400, response.status_code
        mine.refresh_from_db()
        assert mine.parent_id is None

    def test_a_comment_cannot_be_created_on_a_draft(self):
        draft = BlogPostFactory(is_published=False)
        user = UserAccountFactory(num_addresses=0)

        client = APIClient()
        client.force_authenticate(user=user)
        response = client.post(
            reverse("blog-comment-list"),
            {"post": draft.pk, "translations": {"en": {"content": "hi"}}},
            format="json",
        )

        assert response.status_code == 400, response.status_code
        assert not BlogComment.objects.filter(post=draft).exists()

    def test_thread_ancestors_on_a_hidden_post_are_not_returned(self):
        """The read gate stands on its own, whatever wrote the row.

        The admin exposes `post` and `parent` as free fields, so a
        cross-post reply remains creatable by a staff mistake even with
        the API path closed — which is exactly why this is gated in two
        places rather than one.
        """
        hidden = self._hidden_comment()
        attacker = UserAccountFactory(num_addresses=0)
        mine = self._my_comment(attacker)
        # Through the model, not the serializer: this is the write the
        # admin can still make. It must be `save()` — MPTT has to
        # rebuild the tree or `get_ancestors()` walks nothing and this
        # test would pass against the unfixed code too. And both rows
        # must be re-read first: MPTT renumbers `tree_id` in SQL and
        # leaves the in-memory instances stale (both said 1 while the
        # table said 1 and 2), so the move is refused as "a child of its
        # own descendant".
        hidden.refresh_from_db()
        mine.refresh_from_db()
        mine.parent = hidden
        mine.save()

        client = APIClient()
        client.force_authenticate(user=attacker)
        response = client.get(
            reverse("blog-comment-thread", kwargs={"pk": mine.pk})
        )

        assert response.status_code == 200
        rows = response.data.get("results", response.data)
        assert [row["id"] for row in rows] == [mine.pk], (
            "the hidden comment came back as an ancestor"
        )

    def test_replies_on_a_hidden_post_are_not_returned(self):
        """Same gate on the children side."""
        root = self._my_comment(UserAccountFactory(num_addresses=0))
        draft = BlogPostFactory(is_published=False)
        child = BlogCommentFactory(
            post=draft,
            user=UserAccountFactory(num_addresses=0),
            parent=root,
            approved=True,
        )

        response = APIClient().get(
            reverse("blog-comment-replies", kwargs={"pk": root.pk})
        )

        assert response.status_code == 200
        rows = response.data.get("results", response.data)
        assert child.pk not in [row["id"] for row in rows]

    def test_a_reply_on_a_visible_post_still_comes_back(self):
        """The gate must not have emptied the endpoints."""
        user = UserAccountFactory(num_addresses=0)
        root = self._my_comment(user)
        child = BlogCommentFactory(
            post=root.post,
            user=UserAccountFactory(num_addresses=0),
            parent=root,
            approved=True,
        )

        client = APIClient()
        replies = client.get(
            reverse("blog-comment-replies", kwargs={"pk": root.pk})
        )
        thread = client.get(
            reverse("blog-comment-thread", kwargs={"pk": child.pk})
        )

        assert [
            row["id"] for row in replies.data.get("results", replies.data)
        ] == [child.pk]
        assert sorted(
            row["id"] for row in thread.data.get("results", thread.data)
        ) == sorted([root.pk, child.pk])
