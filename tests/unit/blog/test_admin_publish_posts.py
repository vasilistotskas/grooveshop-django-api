"""Bulk "Publish selected posts" keeps a scheduled post's date.

A scheduled post is published with a future date and goes live when the
date passes. Publishing it "now" in bulk would silently discard that
schedule, so the action moves drafts only and reports the scheduled posts
it left alone.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.messages import get_messages
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from blog.admin import BlogPostAdmin
from blog.factories.post import BlogPostFactory
from blog.models import BlogPost
from user.factories.account import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]


@pytest.fixture
def posts():
    now = timezone.now()
    return {
        "draft": BlogPostFactory(is_published=False, published_at=None),
        "scheduled": BlogPostFactory(
            is_published=True, published_at=now + timedelta(days=1)
        ),
        "live": BlogPostFactory(
            is_published=True, published_at=now - timedelta(days=3)
        ),
    }


def _assert_only_the_draft_moved(posts, before):
    draft = BlogPost.objects.get(pk=posts["draft"].pk)
    assert draft.is_published
    assert draft.published_at is not None
    assert draft.published_at <= timezone.now()

    scheduled = BlogPost.objects.get(pk=posts["scheduled"].pk)
    assert scheduled.published_at == before["scheduled"]
    assert scheduled.is_published
    assert scheduled not in BlogPost.objects.published()

    live = BlogPost.objects.get(pk=posts["live"].pk)
    assert live.published_at == before["live"]


def test_scheduled_queryset_is_the_complement_of_published(posts):
    draft_in_future = BlogPostFactory(
        is_published=False, published_at=timezone.now() + timedelta(days=1)
    )

    assert list(BlogPost.objects.scheduled()) == [posts["scheduled"]]
    assert draft_in_future not in BlogPost.objects.scheduled()
    assert posts["live"] in BlogPost.objects.published()
    assert posts["scheduled"] not in BlogPost.objects.published()


def test_the_action_publishes_drafts_only(posts):
    before = {k: p.published_at for k, p in posts.items()}
    admin = BlogPostAdmin(BlogPost, AdminSite())
    request = RequestFactory().post("/admin/")
    request.user = UserAccountFactory(admin=True)
    sent: list[tuple[str, int | None]] = []
    admin.message_user = lambda request, message, level=None: sent.append(
        (str(message), level)
    )

    admin.publish_posts(request, BlogPost.objects.all())

    _assert_only_the_draft_moved(posts, before)
    assert "1 posts were successfully published." in sent[0][0]
    assert "1 scheduled posts were left scheduled" in sent[1][0]


def test_the_changelist_post_publishes_drafts_only(posts):
    before = {k: p.published_at for k, p in posts.items()}
    client = Client()
    client.force_login(
        UserAccountFactory(admin=True),
        backend="tenant.auth_backends.PlatformStaffBackend",
    )

    response = client.post(
        reverse("admin:blog_blogpost_changelist"),
        {
            "action": "publish_posts",
            "_selected_action": [p.pk for p in posts.values()],
        },
        follow=True,
    )

    assert response.status_code == 200
    _assert_only_the_draft_moved(posts, before)
    messages = [str(m) for m in get_messages(response.wsgi_request)]
    assert any("1 posts were successfully published." in m for m in messages)
    assert any(
        "1 scheduled posts were left scheduled" in m and "Published At" in m
        for m in messages
    )


def test_no_scheduled_posts_means_no_warning():
    draft = BlogPostFactory(is_published=False, published_at=None)
    client = Client()
    client.force_login(
        UserAccountFactory(admin=True),
        backend="tenant.auth_backends.PlatformStaffBackend",
    )

    response = client.post(
        reverse("admin:blog_blogpost_changelist"),
        {"action": "publish_posts", "_selected_action": [draft.pk]},
        follow=True,
    )

    messages = [str(m) for m in get_messages(response.wsgi_request)]
    assert not any("left scheduled" in m for m in messages)
    assert BlogPost.objects.get(pk=draft.pk).is_published
