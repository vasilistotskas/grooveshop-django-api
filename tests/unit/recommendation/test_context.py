"""The per-tenant facts strategies consult, and the two properties
the request path depends on: the build costs a FIXED number of
queries, and it is read once per five minutes, not once per request.
"""

from __future__ import annotations

import pytest
from django.contrib.contenttypes.models import ContentType

from product.factories.product import ProductFactory
from recommendation.context import (
    build_tenant_context,
    invalidate_tenant_context,
    tenant_context,
)
from tag.factories.tagged_item import TaggedProductFactory
from tests.utils import count_queries

pytestmark = pytest.mark.django_db


def test_counts_only_sellable_products_and_sees_tags():
    ProductFactory(active=True, num_images=0, num_reviews=0)
    ProductFactory(active=False, num_images=0, num_reviews=0)
    assert build_tenant_context().has_tags is False

    tagged = ProductFactory(active=True, num_images=0, num_reviews=0)
    TaggedProductFactory(content_object=tagged)

    ctx = build_tenant_context()
    assert ctx.product_count == 2
    assert ctx.has_tags is True


def test_build_cost_does_not_depend_on_the_content_type_cache():
    """django-tenants clears ``ContentType``'s cache on every schema
    switch, so a ``get_for_model`` in the build would be one query or
    none depending on what ran before — and a query-budget test that
    compares two requests would flap on it."""
    ContentType.objects.clear_cache()
    with count_queries() as cold:
        build_tenant_context()
    with count_queries() as warm:
        build_tenant_context()

    assert cold.count == warm.count


def test_context_is_cached_until_invalidated():
    tenant_context()
    with count_queries() as cached:
        tenant_context()
    assert cached.count == 0

    invalidate_tenant_context()
    with count_queries() as rebuilt:
        tenant_context()
    assert rebuilt.count > 0
