"""Tests for ``tenant.cache.make_tenant_key``.

``tenant_resolve`` / ``tenant_domains`` are written from whatever
schema the REQUEST happened to resolve to (any tenant's domain can ask
to resolve a *different* domain), while the invalidating signals in
``tenant/signals.py`` always run in the public schema (admin/API
mutations of ``TenantDomain``/``Tenant`` are gated there). Without the
schema-independent ``"global:"`` key family, the invalidating delete
almost never matches the key the write produced, and a changed/deleted
domain keeps resolving from a stale cache entry.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.cache import cache

from tenant.cache import GLOBAL_CACHE_PREFIX, make_tenant_key
from tenant.models import TenantDomain


class TestMakeTenantKey:
    def test_regular_key_is_schema_prefixed(self):
        with patch("tenant.cache.connection") as conn:
            conn.schema_name = "tenant_a"
            assert (
                make_tenant_key("some-key", "redis", 1)
                == "tenant_a:redis:1:some-key"
            )

    def test_global_key_uses_literal_global_segment(self):
        with patch("tenant.cache.connection") as conn:
            conn.schema_name = "tenant_a"
            assert (
                make_tenant_key("global:tenant_resolve:x.com", "redis", 1)
                == "global:redis:1:global:tenant_resolve:x.com"
            )

    def test_global_key_identical_across_schemas(self):
        key = f"{GLOBAL_CACHE_PREFIX}tenant_resolve:x.com"
        with patch("tenant.cache.connection") as conn:
            conn.schema_name = "public"
            public_key = make_tenant_key(key, "redis", 1)
        with patch("tenant.cache.connection") as conn:
            conn.schema_name = "tenant_a"
            tenant_key = make_tenant_key(key, "redis", 1)
        assert public_key == tenant_key

    def test_regular_key_differs_across_schemas(self):
        with patch("tenant.cache.connection") as conn:
            conn.schema_name = "public"
            public_key = make_tenant_key("some-key", "redis", 1)
        with patch("tenant.cache.connection") as conn:
            conn.schema_name = "tenant_a"
            tenant_key = make_tenant_key("some-key", "redis", 1)
        assert public_key != tenant_key

    def test_falls_back_to_public_when_no_schema_name(self):
        with patch("tenant.cache.connection") as conn:
            del conn.schema_name
            assert (
                make_tenant_key("some-key", "redis", 1)
                == "public:redis:1:some-key"
            )


@pytest.mark.django_db
class TestGlobalCacheSchemaIndependence:
    """Round-trips through the REAL cache backend (KEY_FUNCTION is
    ``tenant.cache.make_tenant_key`` even under the test settings — see
    ``tests/conftest.py``) rather than re-testing ``make_tenant_key`` in
    isolation.
    """

    def test_global_key_written_in_tenant_schema_readable_from_public(self):
        key = "global:test_cache_roundtrip:alpha"
        try:
            with patch("tenant.cache.connection") as conn:
                conn.schema_name = "some_tenant"
                cache.set(key, "written-from-tenant", 30)

            with patch("tenant.cache.connection") as conn:
                conn.schema_name = "public"
                assert cache.get(key) == "written-from-tenant"
        finally:
            with patch("tenant.cache.connection") as conn:
                conn.schema_name = "public"
                cache.delete(key)

    def test_global_key_deletable_from_public_after_tenant_write(self):
        key = "global:test_cache_roundtrip:beta"
        with patch("tenant.cache.connection") as conn:
            conn.schema_name = "some_tenant"
            cache.set(key, "value", 30)

        with patch("tenant.cache.connection") as conn:
            conn.schema_name = "public"
            cache.delete(key)

        with patch("tenant.cache.connection") as conn:
            conn.schema_name = "some_tenant"
            assert cache.get(key) is None

    def test_non_global_key_keeps_schema_isolation(self):
        key = "test_cache_roundtrip:isolated"
        try:
            with patch("tenant.cache.connection") as conn:
                conn.schema_name = "tenant_alpha"
                cache.set(key, "alpha-value", 30)

            with patch("tenant.cache.connection") as conn:
                conn.schema_name = "public"
                # Public schema never wrote this key — must not see
                # tenant_alpha's value.
                assert cache.get(key) is None

            with patch("tenant.cache.connection") as conn:
                conn.schema_name = "tenant_alpha"
                assert cache.get(key) == "alpha-value"
        finally:
            with patch("tenant.cache.connection") as conn:
                conn.schema_name = "tenant_alpha"
                cache.delete(key)


@pytest.mark.django_db
class TestSignalInvalidationUsesGlobalKeys:
    """``tenant/signals.py`` fires from the public schema (admin/API
    mutations on ``TenantDomain`` are gated there); the cache entry it
    invalidates may have been written while resolving a request against
    a completely different tenant's domain. Only the schema-independent
    "global:" key family lets that delete actually find the entry.
    """

    def test_domain_save_signal_clears_resolve_cache_written_elsewhere(
        self, tenant_factory
    ):
        tenant = tenant_factory("cache-invalidate-tenant")
        domain = TenantDomain.objects.create(
            tenant=tenant, domain="cache-invalidate.example", is_primary=True
        )

        cache_key = f"global:tenant_resolve:{domain.domain}"
        # Simulate the write happening while resolving a request that
        # arrived on some OTHER tenant's domain.
        with patch("tenant.cache.connection") as conn:
            conn.schema_name = "some_other_tenant"
            cache.set(cache_key, {"schemaName": tenant.schema_name}, 3600)

        assert cache.get(cache_key) is not None

        # The signal fires synchronously on save; re-saving the row
        # (e.g. an admin edit) must invalidate the entry even though the
        # signal itself runs in the public schema.
        domain.domain = "cache-invalidate.example"
        domain.save()

        assert cache.get(cache_key) is None

    def test_domain_save_signal_clears_sibling_domain_resolve_caches(
        self, tenant_factory
    ):
        """Adding a prefixed service row (assets./static./api.) changes
        the ``assetsDomain``/… derivations EMBEDDED in every sibling
        domain's cached resolve payload — most importantly the primary
        domain's. Observed on staging 2026-08-19: a new
        assets-staging row left the primary's cached payload pointing
        at the derived dot-host for the full TTL."""
        tenant = tenant_factory("cache-invalidate-sib")
        primary = TenantDomain.objects.create(
            tenant=tenant,
            domain="cache-invalidate-sib.example",
            is_primary=True,
        )
        primary_key = f"global:tenant_resolve:{primary.domain}"
        cache.set(primary_key, {"schemaName": tenant.schema_name}, 3600)
        assert cache.get(primary_key) is not None

        TenantDomain.objects.create(
            tenant=tenant,
            domain="assets.cache-invalidate-sib.example",
            is_primary=False,
        )

        assert cache.get(primary_key) is None

    def test_domain_delete_signal_clears_resolve_cache_written_elsewhere(
        self, tenant_factory
    ):
        tenant = tenant_factory("cache-invalidate-del")
        domain = TenantDomain.objects.create(
            tenant=tenant,
            domain="cache-invalidate-del.example",
            is_primary=True,
        )
        cache_key = f"global:tenant_resolve:{domain.domain}"

        with patch("tenant.cache.connection") as conn:
            conn.schema_name = "some_other_tenant"
            cache.set(cache_key, {"schemaName": tenant.schema_name}, 3600)

        domain.delete()

        assert cache.get(cache_key) is None

    def test_tenant_save_signal_clears_domains_cache_written_elsewhere(
        self, tenant_factory
    ):
        tenant = tenant_factory("cache-invalidate-domains")
        TenantDomain.objects.create(
            tenant=tenant,
            domain="cache-invalidate-domains.example",
            is_primary=True,
        )
        cache_key = f"global:tenant_domains:{tenant.schema_name}"

        with patch("tenant.cache.connection") as conn:
            conn.schema_name = tenant.schema_name
            cache.set(cache_key, {"cache-invalidate-domains.example"}, 300)

        tenant.name = "Renamed"
        tenant.save()

        assert cache.get(cache_key) is None
