"""Liking a comment is not undone by a broker outage.

The receiver ran inside `.add()`'s atomic block: a `kombu`
`OperationalError` from the publish rolled the like back and answered 500.
It goes through `dispatch_on_commit` now.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from kombu.exceptions import OperationalError

from blog.models import BlogComment
from blog.models.post import BlogPost
from blog.tasks import notify_comment_liked_task
from tenant.models import DeferredDispatch

User = get_user_model()

pytestmark = pytest.mark.django_db


def test_the_like_stays_and_the_notification_is_kept():
    owner = User.objects.create_user(
        email="owner@example.gr", password="pw", username="owner"
    )
    liker = User.objects.create_user(
        email="liker@example.gr", password="pw", username="liker"
    )
    comment = BlogComment.objects.create(
        post=BlogPost.objects.create(), user=owner, approved=True
    )

    with patch.object(
        notify_comment_liked_task,
        "apply_async",
        side_effect=OperationalError("broker unreachable"),
    ):
        comment.likes.add(liker)

    assert comment.likes.filter(pk=liker.pk).exists()
    row = DeferredDispatch.objects.get()
    assert row.task_name == notify_comment_liked_task.name
    assert row.kwargs["comment_id"] == comment.id
    assert row.kwargs["liker_user_ids"] == [liker.id]
