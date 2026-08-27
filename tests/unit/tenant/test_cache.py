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


class TestPayWayResolveInvalidation:
    """A pay-way edit must not sit behind the resolve cache.

    ``agent_payment_instruments`` is derived from the tenant's active
    offline pay-ways and folded into the cached resolve payload, so a
    merchant enabling cash-on-delivery would otherwise stay invisible
    to AI agents for the full hour-long TTL. Pay-way rows live in the
    TENANT schema, so the receiver resolves the tenant from the
    connection's schema rather than from the row.
    """

    def test_saving_a_pay_way_triggers_the_purge(self, db):
        """Proves the receiver is actually wired to ``pay_way.PayWay``.

        Asserted through a real save rather than signal introspection so
        the test does not depend on Django's private receiver registry.
        """
        from pay_way.factories import PayWayFactory

        with patch("tenant.signals._purge_resolve_for_current_schema") as purge:
            pay_way = PayWayFactory(
                active=True,
                is_online_payment=False,
                provider_code="cash_on_delivery",
            )
            assert purge.called, "post_save receiver not connected"

            purge.reset_mock()
            pay_way.delete()
            assert purge.called, "post_delete receiver not connected"

    def test_pay_way_change_clears_every_domain_of_its_tenant(
        self, tenant_factory
    ):
        from tenant.signals import invalidate_resolve_on_pay_way_change

        tenant = tenant_factory("payway-invalidate")
        primary = TenantDomain.objects.create(
            tenant=tenant, domain="payway-invalidate.example", is_primary=True
        )
        alias = TenantDomain.objects.create(
            tenant=tenant,
            domain="api.payway-invalidate.example",
            is_primary=False,
        )
        keys = [f"global:tenant_resolve:{d.domain}" for d in (primary, alias)]
        for key in keys:
            cache.set(key, {"schemaName": tenant.schema_name}, 3600)
            assert cache.get(key) is not None

        # The write lands while serving the TENANT's own schema.
        with patch("django.db.connection") as conn:
            conn.schema_name = tenant.schema_name
            invalidate_resolve_on_pay_way_change(None, None)

        for key in keys:
            assert cache.get(key) is None

    def test_public_schema_write_purges_nothing(self):
        """Seed/fixture loads run in the public schema, which owns no
        storefront domains — purging there would be a wasted scan."""
        from tenant.signals import invalidate_resolve_on_pay_way_change

        key = "global:tenant_resolve:untouched.example"
        cache.set(key, {"schemaName": "public"}, 3600)

        with patch("django.db.connection") as conn:
            conn.schema_name = "public"
            invalidate_resolve_on_pay_way_change(None, None)

        assert cache.get(key) is not None
