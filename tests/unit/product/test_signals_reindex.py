"""Regression tests for the Meilisearch reindex signals (G0307).

The async dispatch path must collect ProductTranslation PKs with a PLAIN
query — never ``get_meilisearch_queryset()``, whose favourites×reviews
aggregate JOIN would run on every ``Product.save()`` just to gather PKs.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from product.factories.product import ProductFactory
from product.models.product import Product
from product.signals import reindex_product_translations


@pytest.mark.django_db
@override_settings(DEBUG=False)
def test_async_reindex_collects_pks_without_aggregate_join():
    product = ProductFactory()

    with (
        override_settings(
            MEILISEARCH={"OFFLINE": False, "ASYNC_INDEXING": True}
        ),
        patch("meili.tasks.index_document_task.apply_async") as mock_dispatch,
        CaptureQueriesContext(connection) as ctx,
    ):
        reindex_product_translations(sender=Product, instance=product)

    # The dispatch fired for the product's translations (proves we didn't
    # early-return and actually walked the PK path). `apply_async`, not
    # `delay`: reindex goes through `dispatch_on_commit`, which stamps the
    # tenant schema at registration rather than when the hook fires.
    assert mock_dispatch.called
    assert all(
        call.kwargs["headers"] == {"_schema_name": connection.schema_name}
        for call in mock_dispatch.call_args_list
    )

    # No captured query may carry the review-average aggregate — that marker
    # is unique to get_meilisearch_queryset() and must not appear on the
    # save-path PK collection.
    all_sql = " ".join(q["sql"].lower() for q in ctx.captured_queries)
    assert "avg(" not in all_sql
