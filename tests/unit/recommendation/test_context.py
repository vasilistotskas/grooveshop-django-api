"""The per-tenant facts strategies consult, and the two properties
the request path depends on: the build costs a FIXED number of
queries, and it is read once per five minutes, not once per request.
"""

from __future__ import annotations

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test.utils import CaptureQueriesContext

from product.factories.product import ProductFactory
from recommendation.context import (
    build_tenant_context,
    invalidate_tenant_context,
    recommendations_active,
    tenant_context,
)
from tag.factories.tagged_item import TaggedProductFactory
from tests.utils.staff import (
    bind_store_tenant,
    store_tenant,
    unbind_store_tenant,
)
from tests.utils.store_settings import switch_store_settings

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
    # The connection's first cursor also sets django-tenants'
    # search_path; pay that before measuring, as a live one has.
    build_tenant_context()
    ContentType.objects.clear_cache()
    with CaptureQueriesContext(connection) as cold:
        build_tenant_context()
    with CaptureQueriesContext(connection) as warm:
        build_tenant_context()

    assert len(cold) == len(warm)


def test_context_is_cached_until_invalidated():
    tenant_context()
    with CaptureQueriesContext(connection) as cached:
        tenant_context()
    assert len(cached) == 0

    invalidate_tenant_context()
    with CaptureQueriesContext(connection) as rebuilt:
        tenant_context()
    assert len(rebuilt) > 0


class TestRecommendationsActive:
    """The gate for the paths with no request to run a permission on —
    the order email and the nightly jobs: the same plan flag and store
    switch the read endpoint enforces."""

    @pytest.fixture
    def store(self, request):
        tenant = store_tenant(
            f"recs_active_{request.param}",
            recommendations_enabled=request.param == "plan_on",
        )
        previous = bind_store_tenant(tenant)
        yield tenant
        unbind_store_tenant(previous)

    @pytest.mark.parametrize("store", ["plan_on"], indirect=True)
    def test_on_when_the_plan_and_the_store_both_say_so(self, store):
        switch_store_settings(PRODUCT_SUGGESTIONS_ENABLED=True)
        assert recommendations_active() is True

    @pytest.mark.parametrize("store", ["plan_on"], indirect=True)
    def test_off_when_the_store_switched_suggestions_off(self, store):
        switch_store_settings(PRODUCT_SUGGESTIONS_ENABLED=False)
        assert recommendations_active() is False

    @pytest.mark.parametrize("store", ["plan_off"], indirect=True)
    def test_off_when_the_plan_does_not_include_them(self, store):
        switch_store_settings(PRODUCT_SUGGESTIONS_ENABLED=True)
        assert recommendations_active() is False

    def test_the_store_switch_ships_off(self):
        assert recommendations_active() is False
