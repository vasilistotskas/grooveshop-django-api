"""A comment answer satisfies the schema the storefront generates from.

The storefront answers 422 to any body its generated Zod rejects. A reply
was saved but showed as failed because ``parentComment.user`` and
``ancestorsPath[].user`` were documented as the full account (``UserDetails``:
``pk``, ``email``, ``isStaff`` ...) while the body carries the public author
identity, the same one the comment's own ``user`` has.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from blog.factories.author import BlogAuthorFactory
from blog.factories.comment import BlogCommentFactory
from blog.factories.post import BlogPostFactory
from tests.utils.openapi import assert_matches_response
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

COMMENT = "/api/v1/blog/comment/{id}"


@pytest.fixture
def thread():
    reader = UserAccountFactory()
    post = BlogPostFactory(
        author=BlogAuthorFactory(user=UserAccountFactory()),
        num_tags=0,
        num_comments=0,
    )
    root = BlogCommentFactory(
        approved=True, post=post, user=UserAccountFactory()
    )
    reply = BlogCommentFactory(
        approved=True, post=post, user=UserAccountFactory(), parent=root
    )
    nested = BlogCommentFactory(
        approved=True, post=post, user=UserAccountFactory(), parent=reply
    )
    client = APIClient()
    client.force_authenticate(user=reader)
    return client, post, root, reply, nested


def test_a_new_reply_matches_the_schema(openapi_schema, thread):
    client, post, root, *_ = thread
    translations = {
        lang["code"]: {"content": f"A reply in {lang['code']}"}
        for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
    }

    response = client.post(
        reverse("blog-comment-list"),
        {"post": post.id, "parent": root.id, "translations": translations},
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert_matches_response(
        openapi_schema,
        "/api/v1/blog/comment",
        response.json(),
        method="post",
        status="201",
    )


@pytest.mark.parametrize("which", ["reply", "nested"])
def test_a_reply_read_back_matches_the_schema(openapi_schema, thread, which):
    client, _post, _root, reply, nested = thread
    comment = {"reply": reply, "nested": nested}[which]

    response = client.get(reverse("blog-comment-detail", args=[comment.pk]))

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["parentComment"] is not None
    assert_matches_response(openapi_schema, COMMENT, body)


def test_a_stranger_sees_no_private_account_field_of_an_author(thread):
    client, _post, _root, _reply, nested = thread

    body = client.get(reverse("blog-comment-detail", args=[nested.pk])).json()

    private = {"email", "phone", "pk", "isStaff", "isSuperuser", "uuid"}
    assert private.isdisjoint(body["parentComment"]["user"])
    for ancestor in body["ancestorsPath"]:
        assert private.isdisjoint(ancestor["user"])
