"""The cache, search and loyalty hooks survive a broker outage.

These publishes follow a change that is already committed and are repaired
or bounded by something else (a TTL, the nightly sync), but they used to
raise out of an `on_commit` hook: a request answered 500 for work that was
done and the transaction's later hooks were dropped. They go through
`dispatch_on_commit` now: a `kombu` `OperationalError` is kept as a
`DeferredDispatch` row, raises nothing, and the row carries the schema that
was registered with the change, not whatever the connection says when the
hook fires.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from kombu.exceptions import OperationalError

from core.cache import edge, invalidation
from core.tasks import invalidate_cache_surfaces_task, purge_edge_cache_task
from loyalty.signals import notify_tier_up_live
from loyalty.tasks import notify_loyalty_tier_up_live
from meili.tasks import index_document_task
from product.factories.product import ProductFactory
from product.factories.product_attribute import ProductAttributeFactory
from product.models.product import ProductTranslation
from product.models.product_attribute import ProductAttribute
from product.signals import reindex_products_by_pk
from promotion import offer_sync
from promotion.tasks import reindex_offer_products_task
from tenant.models import DeferredDispatch
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

PINNED = "pinned_store"


def broker_down(task):
    return patch.object(
        task, "apply_async", side_effect=OperationalError("broker unreachable")
    )


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


def test_a_cache_flush_keeps_its_purge_and_the_schema_it_was_registered_in():
    invalidation._pending_codes().add("pay_way")

    with broker_down(invalidate_cache_surfaces_task):
        invalidation._flush(PINNED)

    row = DeferredDispatch.objects.get()
    assert row.task_name == invalidate_cache_surfaces_task.name
    assert row.headers["_schema_name"] == PINNED
    assert row.options == {"countdown": invalidation.PURGE_DELAY_SECONDS}


def test_an_edge_purge_is_kept_when_the_broker_is_down():
    with broker_down(purge_edge_cache_task):
        assert edge.schedule_purge() is True

    row = DeferredDispatch.objects.get()
    assert row.task_name == purge_edge_cache_task.name
    assert row.options == {"countdown": edge.PURGE_DELAY_SECONDS}


def test_an_offer_refresh_is_kept_with_its_schema():
    with broker_down(reindex_offer_products_task):
        offer_sync._enqueue(PINNED, ["1"], [])

    row = DeferredDispatch.objects.get()
    assert row.task_name == reindex_offer_products_task.name
    assert row.headers["_schema_name"] == PINNED


def test_the_offer_refresh_marks_survive_the_failed_publish():
    """The Redis marks are written before the publish; the swept task reads
    them, so a failed publish must not lose them."""
    with broker_down(reindex_offer_products_task):
        offer_sync._enqueue(connection.schema_name, ["7"], [])

    assert cache.pop_set(offer_sync._PENDING_PROMOTIONS_KEY) == {"7"}


def test_a_product_reindex_keeps_every_document_when_the_broker_is_down():
    product = ProductFactory(active=True, num_images=0, num_reviews=0)
    expected = set(
        ProductTranslation.objects.filter(master=product).values_list(
            "pk", flat=True
        )
    )
    assert expected

    with (
        override_settings(
            MEILISEARCH={"OFFLINE": False, "ASYNC_INDEXING": True}
        ),
        broker_down(index_document_task),
    ):
        assert reindex_products_by_pk([product.pk]) == len(expected)

    rows = DeferredDispatch.objects.filter(task_name=index_document_task.name)
    assert {row.kwargs["pk"] for row in rows} == expected


def test_a_tier_up_notification_is_kept_with_the_schema_of_the_change():
    user = UserAccountFactory()

    with broker_down(notify_loyalty_tier_up_live):
        notify_tier_up_live(
            sender=type(user),
            user=user,
            direction="up",
            old_tier_id=None,
            new_tier_id=None,
            schema_name=PINNED,
        )

    row = DeferredDispatch.objects.get()
    assert row.task_name == notify_loyalty_tier_up_live.name
    assert row.args == [user.pk]
    assert row.headers["_schema_name"] == PINNED


def test_an_attribute_change_keeps_every_document_when_the_broker_is_down():
    from product.signals import update_product_search_index_on_attribute_change

    product = ProductFactory(active=True, num_images=0, num_reviews=0)
    expected = set(
        ProductTranslation.objects.filter(master=product).values_list(
            "pk", flat=True
        )
    )
    attribute = ProductAttributeFactory(product=product)

    with (
        override_settings(
            MEILISEARCH={"OFFLINE": False, "ASYNC_INDEXING": True}
        ),
        broker_down(index_document_task),
    ):
        update_product_search_index_on_attribute_change(
            sender=ProductAttribute, instance=attribute
        )

    rows = DeferredDispatch.objects.filter(task_name=index_document_task.name)
    assert {row.kwargs["pk"] for row in rows} == expected
