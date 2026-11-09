"""A post dated in the future is not searchable until its date, and is
indexed when the date passes with no save."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.conf import settings
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from blog.factories.post import BlogPostFactory
from blog.models.post import BlogPost, BlogPostTranslation
from blog.signals import reindex_blog_post_translations
from core.publication import went_live
from page_config.models import ContentPage

pytestmark = pytest.mark.django_db


@contextmanager
def dispatched():
    """Indexing switched on, every dispatch caught."""
    online = {**settings.MEILISEARCH, "OFFLINE": False, "ASYNC_INDEXING": True}
    with (
        override_settings(MEILISEARCH=online),
        patch("meili.tasks.index_document_task.apply_async") as dispatch,
    ):
        yield dispatch


def test_a_post_dated_ahead_is_not_indexed():
    post = BlogPostFactory(
        is_published=True,
        published_at=timezone.now() + timedelta(hours=1),
    )

    assert not any(t.meili_filter() for t in post.translations.all())


def test_a_live_post_is_indexed():
    post = BlogPostFactory(is_published=True)

    assert all(t.meili_filter() for t in post.translations.all())


def test_going_live_dispatches_its_translations():
    post = BlogPostFactory(is_published=True)
    expected = set(
        BlogPostTranslation.objects.filter(master=post).values_list(
            "pk", flat=True
        )
    )
    assert expected

    with dispatched() as dispatch:
        went_live.send(sender=BlogPost, instance=post)

    assert {
        call.kwargs["kwargs"]["pk"] for call in dispatch.call_args_list
    } == (expected)


def test_another_model_going_live_dispatches_nothing():
    post = BlogPostFactory(is_published=True)

    with dispatched() as dispatch:
        went_live.send(sender=ContentPage, instance=post)

    dispatch.assert_not_called()


@override_settings(DEBUG=False)
def test_a_save_collects_the_pks_without_the_likes_join():
    post = BlogPostFactory(is_published=True)

    with dispatched(), CaptureQueriesContext(connection) as ctx:
        reindex_blog_post_translations(sender=BlogPost, instance=post)

    # Django drops the unread COUNT but keeps its join and GROUP BY.
    assert not any(
        "blog_blogpost_likes" in query["sql"] or "GROUP BY" in query["sql"]
        for query in ctx
    )
