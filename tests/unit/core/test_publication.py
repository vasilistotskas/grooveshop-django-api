"""A row dated in the future goes live with no save: the publication sweep
does what that save would have done, once, and only for those rows."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.utils import timezone

from blog.factories.post import BlogPostFactory
from blog.models.post import BlogPost
from core import publication

pytestmark = pytest.mark.django_db

CURSOR_KEY = "publication:go-live-cursor"


@pytest.fixture(autouse=True)
def no_cursor():
    cache.delete(CURSOR_KEY)
    yield
    cache.delete(CURSOR_KEY)


@pytest.fixture
def stale():
    with patch.object(publication, "mark_surface_stale") as marked:
        yield marked


@pytest.fixture
def live():
    sent: list[tuple[type, object]] = []

    def receiver(sender, instance, **kwargs):
        sent.append((sender, instance))

    publication.went_live.connect(receiver, weak=False)
    yield sent
    publication.went_live.disconnect(receiver)


def scheduled(minutes: int = 5):
    """A post saved now, dated *minutes* ahead."""
    return BlogPostFactory(
        is_published=True,
        published_at=timezone.now() + timedelta(minutes=minutes),
    )


class TestReleaseDuePublications:
    def test_a_post_whose_date_passed_goes_live(self, stale, live):
        post = scheduled()
        cache.set(CURSOR_KEY, timezone.now().isoformat())

        released = publication.release_due_publications(
            now=post.published_at + timedelta(seconds=30)
        )

        assert released == 1
        assert live == [(BlogPost, post)]
        marked = {call.args[0] for call in stale.call_args_list}
        assert {"blog", "sitemap_seo"} <= marked

    def test_a_date_still_ahead_waits(self, stale, live):
        post = scheduled()
        cache.set(CURSOR_KEY, timezone.now().isoformat())

        released = publication.release_due_publications(
            now=post.published_at - timedelta(seconds=30)
        )

        assert released == 0
        assert live == []
        stale.assert_not_called()

    def test_a_date_already_swept_is_not_repeated(self, stale, live):
        post = scheduled()
        cache.set(CURSOR_KEY, timezone.now().isoformat())
        after = post.published_at + timedelta(seconds=30)
        publication.release_due_publications(now=after)
        live.clear()
        stale.reset_mock()

        released = publication.release_due_publications(
            now=after + timedelta(minutes=1)
        )

        assert released == 0
        assert live == []
        stale.assert_not_called()

    def test_a_post_saved_after_its_date_is_left_to_that_save(
        self, stale, live
    ):
        # Dated in the past at its save: the save purged and announced.
        BlogPostFactory(
            is_published=True,
            published_at=timezone.now() - timedelta(minutes=1),
        )
        cache.set(CURSOR_KEY, (timezone.now() - timedelta(hours=1)).isoformat())

        assert publication.release_due_publications() == 0
        assert live == []

    def test_an_unpublished_post_never_goes_live(self, stale, live):
        post = BlogPostFactory(
            is_published=False,
            published_at=timezone.now() + timedelta(minutes=5),
        )
        cache.set(CURSOR_KEY, timezone.now().isoformat())

        released = publication.release_due_publications(
            now=post.published_at + timedelta(seconds=30)
        )

        assert released == 0
        assert live == []

    def test_a_failed_run_repeats_its_window(self, stale, live):
        post = scheduled()
        start = timezone.now().isoformat()
        cache.set(CURSOR_KEY, start)
        after = post.published_at + timedelta(seconds=30)
        stale.side_effect = RuntimeError("purge queue unavailable")

        with pytest.raises(RuntimeError):
            publication.release_due_publications(now=after)

        assert cache.get(CURSOR_KEY) == start
        stale.side_effect = None
        assert publication.release_due_publications(now=after) == 1

    def test_a_missing_cursor_looks_back_a_day(self, stale, live):
        post = scheduled()

        released = publication.release_due_publications(
            now=post.published_at + timedelta(hours=23)
        )

        assert released == 1
        assert live == [(BlogPost, post)]


def test_every_publishable_model_is_swept():
    labels = {model._meta.label for model in publication.publishable_models()}

    assert {
        "blog.BlogPost",
        "page_config.ContentPage",
        "product.ProductReview",
    } <= labels
