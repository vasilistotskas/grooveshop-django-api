"""A shared row (``Country``, ``Region``) lives in the public schema, and
every store caches its own responses of it: a platform edit purges the
surfaces that declare it in every schema, not in ``public`` alone."""

from __future__ import annotations

from unittest import mock

import pytest
from django.core.cache import cache
from django_tenants.utils import schema_context

from core.cache.invalidation import (
    connect_surface_invalidation,
    disconnect_surface_invalidation,
    run_queued_purge,
)
from region.models import Region
from tenant.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _connected():
    connect_surface_invalidation(force=True)
    yield
    disconnect_surface_invalidation()


def test_a_region_edit_purges_every_stores_own_cached_responses(
    mt_tenant, django_capture_on_commit_callbacks
):
    schemas = [
        "public",
        *Tenant.objects.stores().values_list("schema_name", flat=True),
    ]
    assert mt_tenant.schema_name in schemas
    keys = {}
    for schema in schemas:
        with schema_context(schema):
            keys[schema] = "views.decorators.cache.RegionViewSet_list.x"
            cache.set(keys[schema], "cached", 60)

    with (
        mock.patch("core.tasks.invalidate_cache_surfaces_task.apply_async"),
        mock.patch("core.cache.service.nuxt_client.request_purge") as nuxt,
        schema_context("public"),
    ):
        nuxt.return_value = mock.Mock(
            matched=0, deleted=0, blocked=0, error=None
        )
        with django_capture_on_commit_callbacks(execute=True):
            Region.objects.create(
                alpha="GR-QZ", country_id="GR", sort_order=999
            )
        # What the queued task does, in each schema it was queued for.
        for schema in schemas:
            with schema_context(schema):
                assert "regions_countries" in run_queued_purge()

    for schema in schemas:
        with schema_context(schema):
            assert cache.get(keys[schema]) is None
