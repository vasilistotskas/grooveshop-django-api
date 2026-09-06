"""A bulk UPDATE emits no post_save, so nothing reindexes on its own.

`queryset.update()` and `SoftDeleteQuerySet.delete()` are single SQL
statements. Measured with a spy receiver:

    bulk .update()     -> post_save fired: 0
    instance .save()   -> post_save fired: 1
    queryset .delete() -> post_save fired: 0

`active` is an indexed, filterable field and `search/views.py` filters
the INDEXED value — so "Deactivate selected products" left every one of
them fully searchable and buyable until the nightly sync, while the
admin reported success.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import override_settings

from product.factories.product import ProductFactory
from product.models.product import Product, ProductTranslation
from product.signals import reindex_products_by_pk

pytestmark = pytest.mark.django_db


def test_a_bulk_update_reindexes_every_affected_translation():
    # Fixtures FIRST, under the suite's default OFFLINE=True. Building
    # them with OFFLINE=False makes every factory save attempt a real
    # Meilisearch call and wait out MEILISEARCH["TIMEOUT"] (30s) each —
    # which is what took this one test from 0.1s to 42s.
    products = [
        ProductFactory(active=True, num_images=0, num_reviews=0)
        for _ in range(3)
    ]
    ids = [p.pk for p in products]
    expected = set(
        ProductTranslation.objects.filter(master_id__in=ids).values_list(
            "pk", flat=True
        )
    )
    assert expected, "the fixture produced no translations"

    with (
        override_settings(
            MEILISEARCH={"OFFLINE": False, "ASYNC_INDEXING": True}
        ),
        patch("meili.tasks.index_document_task.delay") as dispatch,
    ):
        Product.objects.filter(pk__in=ids).update(active=False)
        dispatched = reindex_products_by_pk(ids)

    assert dispatched == len(expected)
    assert {call.kwargs["pk"] for call in dispatch.call_args_list} == expected


def test_no_products_dispatches_nothing():
    with (
        override_settings(
            MEILISEARCH={"OFFLINE": False, "ASYNC_INDEXING": True}
        ),
        patch("meili.tasks.index_document_task.delay") as dispatch,
    ):
        assert reindex_products_by_pk([]) == 0

    dispatch.assert_not_called()


@override_settings(MEILISEARCH={"OFFLINE": True})
def test_offline_dispatches_nothing():
    product = ProductFactory(active=True, num_images=0, num_reviews=0)

    with patch("meili.tasks.index_document_task.delay") as dispatch:
        assert reindex_products_by_pk([product.pk]) == 0

    dispatch.assert_not_called()


def test_the_admin_action_reindexes_what_it_deactivated():
    """Through the action, not just the helper."""
    from django.contrib.admin.sites import AdminSite
    from django.test import RequestFactory

    from product.admin import ProductAdmin

    products = [
        ProductFactory(active=True, num_images=0, num_reviews=0)
        for _ in range(2)
    ]
    ids = [p.pk for p in products]
    request = RequestFactory().post("/admin/")
    request.user = None
    admin_instance = ProductAdmin(Product, AdminSite())

    with (
        override_settings(
            MEILISEARCH={"OFFLINE": False, "ASYNC_INDEXING": True}
        ),
        patch("meili.tasks.index_document_task.delay") as dispatch,
        patch.object(ProductAdmin, "message_user"),
    ):
        admin_instance.make_inactive(
            request, Product.objects.filter(pk__in=ids)
        )

    assert not Product.objects.filter(pk__in=ids, active=True).exists()
    assert dispatch.called, (
        "the products were deactivated but their documents were not "
        "reindexed — they stay searchable until the nightly sync"
    )
