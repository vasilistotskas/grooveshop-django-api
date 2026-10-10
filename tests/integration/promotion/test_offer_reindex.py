"""Keeping the index's ``on_offer`` flag fresh.

Nothing here talks to Meilisearch: the reindex entry point
(``product.signals.reindex_products_by_pk``) is patched and the tests
assert WHICH products it is handed and HOW OFTEN it is reached.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from djmoney.money import Money

from product.factories import ProductFactory
from product.factories.category import ProductCategoryFactory
from promotion import offer_sync
from promotion.enum import PromotionTrigger, TargetScope
from promotion.factories.promotion import PromotionFactory
from promotion.models import PromotionRedemption
from promotion.tasks import (
    reindex_offer_products_task,
    reindex_offer_window_changes_task,
)


def _indexing(enabled: bool):
    """Flip the producers' own gate, not ``MEILISEARCH["OFFLINE"]``.

    The setting also gates ``Product.post_save``'s indexing, which would
    reach for an engine the factories below do not have.
    """
    return (
        patch("promotion.offer_sync.indexing_enabled", return_value=enabled),
        patch("promotion.signals.indexing_enabled", return_value=enabled),
    )


@pytest.fixture(autouse=True)
def indexing_on(db):
    on_sync, on_signals = _indexing(True)
    with on_sync, on_signals:
        cache.clear()
        yield
        cache.clear()


@pytest.fixture
def reindexed():
    with patch(
        "product.signals.reindex_products_by_pk", return_value=0
    ) as mock:
        yield mock


@pytest.fixture(autouse=True)
def scheduled():
    with patch.object(
        reindex_offer_products_task, "apply_async"
    ) as apply_async:
        yield apply_async


def _ids(mock) -> set[int]:
    """The union of product ids the patched reindex was called with."""
    return {pk for call in mock.call_args_list for pk in call.args[0]}


def _promotion(**kwargs):
    kwargs.setdefault("trigger", PromotionTrigger.AUTOMATIC)
    kwargs.setdefault("target_scope", TargetScope.PRODUCTS)
    kwargs.setdefault("is_active", True)
    return PromotionFactory(**kwargs)


@pytest.mark.django_db
class TestCoalescing:
    def test_a_dozen_marks_schedule_one_task(
        self, django_capture_on_commit_callbacks, scheduled
    ):
        product = ProductFactory(active=True)
        category = ProductCategoryFactory()
        with django_capture_on_commit_callbacks(execute=True):
            promotion = _promotion()
            promotion.products.set([product])
            promotion.categories.set([category])
            promotion.excluded_products.set([product])
            promotion.is_active = False
            promotion.save()

        assert scheduled.call_count == 1
        assert scheduled.call_args.kwargs["countdown"] == (
            offer_sync.REINDEX_DELAY_SECONDS
        )

    def test_a_later_edit_after_the_task_ran_schedules_again(
        self, django_capture_on_commit_callbacks, scheduled, reindexed
    ):
        with django_capture_on_commit_callbacks(execute=True):
            _promotion()
        reindex_offer_products_task()
        with django_capture_on_commit_callbacks(execute=True):
            _promotion()

        assert scheduled.call_count == 2

    def test_offline_engine_queues_nothing(
        self, django_capture_on_commit_callbacks, scheduled
    ):
        off_sync, off_signals = _indexing(False)
        with off_sync, off_signals:
            with django_capture_on_commit_callbacks(execute=True):
                promotion = _promotion()
                promotion.products.set([ProductFactory(active=True)])

        assert scheduled.call_count == 0


@pytest.mark.django_db
class TestSignals:
    def _run(self, django_capture_on_commit_callbacks, reindexed, action):
        with django_capture_on_commit_callbacks(execute=True):
            action()
        reindex_offer_products_task()
        return _ids(reindexed)

    def test_saving_a_promotion_reindexes_its_products(
        self, django_capture_on_commit_callbacks, scheduled, reindexed
    ):
        product = ProductFactory(active=True)
        promotion = _promotion()
        promotion.products.set([product])
        other = ProductFactory(active=True)
        cache.clear()

        def edit():
            promotion.is_active = False
            promotion.save()

        assert self._run(
            django_capture_on_commit_callbacks, reindexed, edit
        ) == {product.id}
        assert other.id not in _ids(reindexed)

    def test_category_scope_reindexes_the_whole_subtree(
        self, django_capture_on_commit_callbacks, scheduled, reindexed
    ):
        root = ProductCategoryFactory()
        child = ProductCategoryFactory(parent=root)
        in_child = ProductFactory(category=child, active=True)
        outside = ProductFactory(category=ProductCategoryFactory(), active=True)
        promotion = _promotion(target_scope=TargetScope.CATEGORIES)

        ids = self._run(
            django_capture_on_commit_callbacks,
            reindexed,
            lambda: promotion.categories.set([root]),
        )

        assert in_child.id in ids
        assert outside.id not in ids

    def test_removing_a_product_reindexes_it(
        self, django_capture_on_commit_callbacks, scheduled, reindexed
    ):
        """The removed product is no longer in the scope afterwards, so
        it has to be captured BEFORE the change."""
        kept = ProductFactory(active=True)
        removed = ProductFactory(active=True)
        promotion = _promotion()
        promotion.products.set([kept, removed])
        cache.clear()

        ids = self._run(
            django_capture_on_commit_callbacks,
            reindexed,
            lambda: promotion.products.remove(removed),
        )

        assert removed.id in ids

    def test_clearing_reindexes_every_former_member(
        self, django_capture_on_commit_callbacks, scheduled, reindexed
    ):
        members = ProductFactory.create_batch(2, active=True)
        promotion = _promotion()
        promotion.products.set(members)
        cache.clear()

        ids = self._run(
            django_capture_on_commit_callbacks,
            reindexed,
            promotion.products.clear,
        )

        assert ids >= {product.id for product in members}

    def test_deleting_a_promotion_reindexes_its_products(
        self, django_capture_on_commit_callbacks, scheduled, reindexed
    ):
        product = ProductFactory(active=True)
        promotion = _promotion()
        promotion.products.set([product])
        cache.clear()

        ids = self._run(
            django_capture_on_commit_callbacks, reindexed, promotion.delete
        )

        assert ids == {product.id}

    def test_exclusion_changes_reindex_the_scope(
        self, django_capture_on_commit_callbacks, scheduled, reindexed
    ):
        product = ProductFactory(active=True)
        promotion = _promotion()
        promotion.products.set([product])
        cache.clear()

        ids = self._run(
            django_capture_on_commit_callbacks,
            reindexed,
            lambda: promotion.excluded_products.add(product),
        )

        assert ids == {product.id}

    def test_the_redemption_that_reaches_the_limit_reindexes(
        self, django_capture_on_commit_callbacks, scheduled, reindexed
    ):
        product = ProductFactory(active=True)
        promotion = _promotion(usage_limit_total=1)
        promotion.products.set([product])
        cache.clear()

        ids = self._run(
            django_capture_on_commit_callbacks,
            reindexed,
            lambda: PromotionRedemption.objects.create(
                promotion=promotion, amount=Money(Decimal(1), "EUR")
            ),
        )

        assert ids == {product.id}

    def test_a_redemption_below_the_limit_does_not(
        self, django_capture_on_commit_callbacks, scheduled, reindexed
    ):
        promotion = _promotion(usage_limit_total=5)
        promotion.products.set([ProductFactory(active=True)])
        cache.clear()
        scheduled.reset_mock()

        with django_capture_on_commit_callbacks(execute=True):
            PromotionRedemption.objects.create(
                promotion=promotion, amount=Money(Decimal(1), "EUR")
            )

        assert scheduled.call_count == 0

    def test_a_redemption_on_an_unlimited_promotion_does_not(
        self, django_capture_on_commit_callbacks, scheduled, reindexed
    ):
        promotion = _promotion(usage_limit_total=None)
        cache.clear()
        scheduled.reset_mock()

        with django_capture_on_commit_callbacks(execute=True):
            PromotionRedemption.objects.create(
                promotion=promotion, amount=Money(Decimal(1), "EUR")
            )

        assert scheduled.call_count == 0


@pytest.mark.django_db
class TestWindowSweep:
    def test_a_window_that_opened_since_the_last_run(self, reindexed):
        product = ProductFactory(active=True)
        now = timezone.now()
        opens = _promotion(starts_at=now - timedelta(minutes=2))
        opens.products.set([product])
        cache.set(
            "promotion-offer:window-cursor",
            (now - timedelta(minutes=5)).isoformat(),
            timeout=None,
        )

        reindex_offer_window_changes_task()

        assert _ids(reindexed) == {product.id}

    def test_a_window_that_closed_since_the_last_run(self, reindexed):
        product = ProductFactory(active=True)
        now = timezone.now()
        closes = _promotion(
            starts_at=now - timedelta(days=1),
            ends_at=now - timedelta(minutes=1),
        )
        closes.products.set([product])
        cache.set(
            "promotion-offer:window-cursor",
            (now - timedelta(minutes=5)).isoformat(),
            timeout=None,
        )

        reindex_offer_window_changes_task()

        assert _ids(reindexed) == {product.id}

    def test_an_edge_already_swept_is_not_repeated(self, reindexed):
        product = ProductFactory(active=True)
        now = timezone.now()
        opens = _promotion(starts_at=now - timedelta(minutes=2))
        opens.products.set([product])
        cache.set(
            "promotion-offer:window-cursor",
            (now - timedelta(minutes=5)).isoformat(),
            timeout=None,
        )

        reindex_offer_window_changes_task()
        reindexed.reset_mock()
        reindex_offer_window_changes_task()

        assert _ids(reindexed) == set()

    def test_an_edge_in_the_future_waits(self, reindexed):
        product = ProductFactory(active=True)
        later = _promotion(starts_at=timezone.now() + timedelta(hours=1))
        later.products.set([product])

        reindex_offer_window_changes_task()

        assert _ids(reindexed) == set()

    def test_an_inactive_promotion_has_no_edge_to_report(self, reindexed):
        product = ProductFactory(active=True)
        off = _promotion(
            is_active=False, starts_at=timezone.now() - timedelta(minutes=1)
        )
        off.products.set([product])

        reindex_offer_window_changes_task()

        assert _ids(reindexed) == set()

    def test_a_missing_cursor_falls_back_to_a_wide_window(self, reindexed):
        product = ProductFactory(active=True)
        opens = _promotion(starts_at=timezone.now() - timedelta(hours=3))
        opens.products.set([product])

        reindex_offer_window_changes_task()

        assert _ids(reindexed) == {product.id}

    def test_the_cursor_does_not_move_when_the_reindex_fails(self, reindexed):
        product = ProductFactory(active=True)
        opens = _promotion(starts_at=timezone.now() - timedelta(minutes=2))
        opens.products.set([product])
        reindexed.side_effect = RuntimeError("meili down")

        with pytest.raises(RuntimeError):
            offer_sync.reindex_window_changes()

        assert cache.get("promotion-offer:window-cursor") is None


def test_the_beat_entry_fans_out_per_tenant():
    from celery.schedules import crontab

    entry = settings.CELERY_BEAT_SCHEDULE["reindex-offer-window-changes"]

    assert entry["task"] == "tenant.tasks.fanout_reindex_offer_window_changes"
    assert entry["schedule"] == crontab(minute="*/5")
