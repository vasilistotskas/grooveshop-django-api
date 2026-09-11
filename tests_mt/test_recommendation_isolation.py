"""The recommendation engine is schema-bound end to end.

``recommendation`` is TENANT_APPS-only, so its tables do not exist in
public at all — a slot seeded for one store cannot be read by another
because the query itself fails outside that store's schema. And the
one thing the engine caches, the per-tenant context under
``recs:tenant_context``, is written through the schema-scoped
``KEY_FUNCTION``: store A's "has no tags, 3 products" can never answer
for store B.
"""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.db import transaction
from django.db.utils import ProgrammingError
from django_tenants.utils import schema_context

from recommendation.context import _CACHE_KEY


@pytest.fixture(autouse=True)
def _cleanup_context_key(mt_tenant):
    yield
    for schema in (mt_tenant.schema_name, "public"):
        with schema_context(schema):
            cache.delete(_CACHE_KEY)


@pytest.mark.django_db
def test_slots_are_schema_local(mt_tenant):
    from recommendation.models import RecommendationSlot
    from recommendation.presets import seed_recommendation_slots

    with schema_context(mt_tenant.schema_name):
        seed_recommendation_slots(mt_tenant.vertical)
        assert RecommendationSlot.objects.exists()

    with schema_context("public"):
        with (
            pytest.raises(ProgrammingError, match="does not exist"),
            transaction.atomic(),
        ):
            RecommendationSlot.objects.exists()


@pytest.mark.django_db
def test_tenant_context_cache_is_schema_scoped(mt_tenant):
    from recommendation.context import tenant_context

    with schema_context(mt_tenant.schema_name):
        tenant_context()
        assert cache.get(_CACHE_KEY) is not None

    with schema_context("public"):
        assert cache.get(_CACHE_KEY) is None, (
            "the tenant context cached inside the tenant schema was "
            "readable from public — it would answer for every store"
        )
