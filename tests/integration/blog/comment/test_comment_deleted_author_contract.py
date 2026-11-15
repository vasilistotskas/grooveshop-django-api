"""A comment outlives the account that wrote it, and the schema says so.

``BlogComment.user`` is ``SET_NULL``: deleting an account keeps its comments
with no author. The body then carries ``"user": null`` (and a reply under
such a comment carried ``{}`` as its parent's and ancestors' author), while
the schema declared a non-null ``UserPublic``. The storefront answers 422 to
any body its generated Zod rejects, so one such comment on a page of results
broke the whole comment list.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from blog.factories.author import BlogAuthorFactory
from blog.factories.comment import BlogCommentFactory
from blog.factories.post import BlogPostFactory
from tests.utils.openapi import assert_matches_response
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def orphaned_thread():
    """An approved comment whose author deleted their account, a reply to
    it and a reply to that reply."""
    post = BlogPostFactory(
        author=BlogAuthorFactory(user=UserAccountFactory()),
        num_tags=0,
        num_comments=0,
    )
    gone = UserAccountFactory()
    root = BlogCommentFactory(approved=True, post=post, user=gone)
    reply = BlogCommentFactory(
        approved=True, post=post, user=UserAccountFactory(), parent=root
    )
    nested = BlogCommentFactory(
        approved=True, post=post, user=UserAccountFactory(), parent=reply
    )
    gone.delete()
    root.refresh_from_db()
    assert root.user is None, "sanity: SET_NULL cleared it"
    return post, root, reply, nested


def test_the_comment_list_matches_the_schema(openapi_schema, orphaned_thread):
    _post, root, *_ = orphaned_thread

    response = APIClient().get(reverse("blog-comment-list"))

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert root.id in {item["id"] for item in body["results"]}
    assert_matches_response(openapi_schema, "/api/v1/blog/comment", body)


def test_the_comment_read_back_matches_the_schema(
    openapi_schema, orphaned_thread
):
    _post, root, *_ = orphaned_thread

    response = APIClient().get(reverse("blog-comment-detail", args=[root.pk]))

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["user"] is None
    assert_matches_response(openapi_schema, "/api/v1/blog/comment/{id}", body)


def test_a_reply_names_no_author_for_the_deleted_one(
    openapi_schema, orphaned_thread
):
    _post, _root, _reply, nested = orphaned_thread

    response = APIClient().get(reverse("blog-comment-detail", args=[nested.pk]))

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["ancestorsPath"][0]["user"] is None
    assert_matches_response(openapi_schema, "/api/v1/blog/comment/{id}", body)


def test_a_reply_to_the_orphan_names_no_parent_author(
    openapi_schema, orphaned_thread
):
    _post, _root, reply, _nested = orphaned_thread

    response = APIClient().get(reverse("blog-comment-detail", args=[reply.pk]))

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["parentComment"]["user"] is None
    assert_matches_response(openapi_schema, "/api/v1/blog/comment/{id}", body)


def test_the_post_comments_match_the_schema(openapi_schema, orphaned_thread):
    post, root, *_ = orphaned_thread

    response = APIClient().get(reverse("blog-post-comments", args=[post.pk]))

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert root.id in {item["id"] for item in body["results"]}
    assert_matches_response(
        openapi_schema, "/api/v1/blog/post/{id}/comments", body
    )
