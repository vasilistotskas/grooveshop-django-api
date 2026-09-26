"""Tests for ``tenant.cache``: key namespacing and generation-keyed
invalidation.

``tenant_resolve`` / ``tenant_domains`` are read from whatever schema
the REQUEST happened to resolve to (any tenant's domain can ask to
resolve a *different* domain), so they live in the schema-independent
``"global:"`` key family. They are invalidated by
``Tenant.cache_generation``, which is part of their keys and which
every write that changes them bumps in the database — never by a Redis
delete, which the fail-open cache drops during an outage.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.cache import cache, caches
from django.db import transaction
from django.test import Client
from redis.backoff import NoBackoff
from redis.retry import Retry

from core.caches import CustomCache
from pay_way.enum.settlement import PaySettlement
from tenant.cache import (
    GLOBAL_CACHE_PREFIX,
    make_tenant_key,
    tenant_domains_key,
    tenant_resolve_key,
)
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


RESOLVE_URL = "/api/v1/tenant/resolve?domain={}"


@pytest.fixture
def redis_down():
    """A real ``CustomCache`` against a port nothing listens on — the
    ``stopped`` outage of ``tests/unit/core/test_cache_fail_open.py``."""
    return CustomCache(
        server="redis://127.0.0.1:1/0",
        params={
            "OPTIONS": {
                "retry": Retry(NoBackoff(), 0),
                "socket_connect_timeout": 0.2,
                "health_check_interval": 0,
            }
        },
    )


@pytest.fixture
def deletes_are_lost(monkeypatch):
    """Every cache delete is the fail-open no-op of an outage, while
    reads and writes still work: exactly what a Redis blip during a
    write leaves behind. Nothing may depend on a delete landing."""
    backend = type(caches["default"])
    monkeypatch.setattr(backend, "delete", lambda *a, **k: False)
    monkeypatch.setattr(backend, "delete_many", lambda *a, **k: None)


def _generation(tenant) -> int:
    return (
        type(tenant)
        .objects.values_list("cache_generation", flat=True)
        .get(pk=tenant.pk)
    )


@pytest.mark.django_db
class TestTenantSaveBumpsTheGeneration:
    """``Tenant.save`` moves the counter in the same UPDATE, relatively,
    so it only ever goes up."""

    def test_an_insert_starts_at_zero(self, tenant_factory):
        tenant = tenant_factory("gen-insert")
        assert tenant.cache_generation == 0
        assert _generation(tenant) == 0

    def test_an_update_bumps_and_the_instance_holds_the_integer(
        self, tenant_factory
    ):
        tenant = tenant_factory("gen-update")
        tenant.store_name = "Renamed"
        tenant.save()

        # Refreshed from RETURNING — an int, not the F() expression.
        assert tenant.cache_generation == 1
        assert _generation(tenant) == 1

    def test_a_narrow_update_bumps_too(self, tenant_factory):
        tenant = tenant_factory("gen-narrow")
        tenant.favicon_url = "https://example.com/f.ico"
        tenant.save(update_fields=["favicon_url"])
        assert _generation(tenant) == 1

    def test_an_empty_update_fields_stays_a_no_op(self, tenant_factory):
        tenant = tenant_factory("gen-empty")
        tenant.save(update_fields=[])
        assert tenant.cache_generation == 0
        assert _generation(tenant) == 0

    def test_a_stale_copy_never_writes_an_older_generation_back(
        self, tenant_factory
    ):
        tenant = tenant_factory("gen-stale")
        stale = type(tenant).objects.get(pk=tenant.pk)

        tenant.save()
        tenant.save()
        assert _generation(tenant) == 2

        # Loaded at 0; an absolute write would reset the counter to a
        # value whose key may still hold a pre-write entry.
        stale.store_name = "From an old copy"
        stale.save()
        assert _generation(tenant) == 3

    def test_a_rolled_back_save_leaves_the_generation(self, tenant_factory):
        tenant = tenant_factory("gen-rollback")
        with pytest.raises(RuntimeError), transaction.atomic():
            tenant.save()
            raise RuntimeError
        assert _generation(tenant) == 0

    def test_a_failed_save_leaves_an_integer_on_the_instance(
        self, tenant_factory
    ):
        tenant = tenant_factory("gen-failed")
        with (
            patch(
                "django.db.models.Model.save",
                side_effect=RuntimeError("boom"),
            ),
            pytest.raises(RuntimeError),
        ):
            tenant.save()
        assert tenant.cache_generation == 0


@pytest.mark.django_db
class TestDomainWritesBumpTheGeneration:
    def test_adding_a_domain_bumps(self, tenant_factory):
        tenant = tenant_factory("gen-domain-add")
        TenantDomain.objects.create(
            tenant=tenant, domain="gen-domain-add.example", is_primary=True
        )
        assert _generation(tenant) == 1

    def test_changing_and_removing_a_domain_bump(self, tenant_factory):
        tenant = tenant_factory("gen-domain-change")
        domain = TenantDomain.objects.create(
            tenant=tenant, domain="gen-domain-change.example", is_primary=True
        )
        domain.domain = "gen-domain-changed.example"
        domain.save()
        assert _generation(tenant) == 2

        domain.delete()
        assert _generation(tenant) == 3

    def test_only_the_owning_tenant_moves(self, tenant_factory):
        owner = tenant_factory("gen-domain-owner")
        bystander = tenant_factory("gen-domain-bystander")
        TenantDomain.objects.create(
            tenant=owner, domain="gen-domain-owner.example", is_primary=True
        )
        assert _generation(owner) == 1
        assert _generation(bystander) == 0


@pytest.mark.django_db
class TestTenantSchemaWritesBumpTheGeneration:
    """Pay-ways and the agent settings live in the TENANT schema but are
    folded into the cached resolve payload, so their receivers bump the
    tenant the connection's schema belongs to."""

    def test_saving_and_deleting_a_pay_way_reach_the_receiver(self):
        """Asserted through a real save rather than signal introspection
        so the test does not depend on Django's private registry."""
        from pay_way.factories import PayWayFactory

        with patch(
            "tenant.signals._bump_generation_for_current_schema"
        ) as bump:
            pay_way = PayWayFactory(
                active=True,
                settlement=PaySettlement.COURIER_CASH,
                provider_code="cash_on_delivery",
            )
            assert bump.called, "post_save receiver not connected"

            bump.reset_mock()
            pay_way.delete()
            assert bump.called, "post_delete receiver not connected"

    def test_a_pay_way_change_bumps_the_schemas_tenant(self, tenant_factory):
        from tenant.signals import bump_generation_on_pay_way_change

        tenant = tenant_factory("gen-payway")
        bystander = tenant_factory("gen-payway-bystander")
        with patch("tenant.signals.connection") as conn:
            conn.schema_name = tenant.schema_name
            bump_generation_on_pay_way_change(None, None)

        assert _generation(tenant) == 1
        assert _generation(bystander) == 0

    def test_an_agent_setting_bumps_and_another_setting_does_not(
        self, tenant_factory
    ):
        from types import SimpleNamespace

        from tenant.signals import bump_generation_on_agent_setting_change

        tenant = tenant_factory("gen-setting")
        with patch("tenant.signals.connection") as conn:
            conn.schema_name = tenant.schema_name
            bump_generation_on_agent_setting_change(
                None, SimpleNamespace(name="STORE_TAGLINE")
            )
            assert _generation(tenant) == 0
            bump_generation_on_agent_setting_change(
                None, SimpleNamespace(name="AGENT_COMMERCE_ENABLED")
            )
        assert _generation(tenant) == 1

    def test_a_public_schema_write_bumps_nothing(self, tenant_factory):
        """Seed and fixture loads run in public, which owns no
        storefront's pay-ways or settings."""
        from tenant.signals import bump_generation_on_pay_way_change

        tenant = tenant_factory("gen-public")
        with patch("tenant.signals.connection") as conn:
            conn.schema_name = "public"
            bump_generation_on_pay_way_change(None, None)
        assert _generation(tenant) == 0


@pytest.mark.django_db
class TestInvalidationNeverDependsOnADelete:
    """The correctness bar: after a committed tenant or domain write, no
    reader is served the pre-write value, even when every delete of the
    write was lost to a Redis blip (``CustomCache`` fails open, so a
    delete during an outage is a logged no-op)."""

    def test_a_changed_store_is_served_although_no_delete_landed(
        self, tenant_factory, deletes_are_lost
    ):
        tenant = tenant_factory("stale-store")
        TenantDomain.objects.create(
            tenant=tenant, domain="stale-store.example", is_primary=True
        )
        client = Client()
        url = RESOLVE_URL.format("stale-store.example")
        before = client.get(url).json()
        tenant.refresh_from_db()
        stale_key = tenant_resolve_key("stale-store.example", tenant)
        assert cache.get(stale_key) is not None

        tenant.store_name = "Renamed during the outage"
        tenant.save()

        after = client.get(url).json()
        assert after["storeName"] == "Renamed during the outage"
        assert after["storeName"] != before["storeName"]
        # The pre-write entry is still in the cache — it is simply
        # never read again, and ages out on its TTL.
        assert cache.get(stale_key) is not None

    def test_a_suspended_store_stops_resolving_at_once(
        self, tenant_factory, deletes_are_lost
    ):
        tenant = tenant_factory("stale-suspended")
        TenantDomain.objects.create(
            tenant=tenant, domain="stale-suspended.example", is_primary=True
        )
        client = Client()
        url = RESOLVE_URL.format("stale-suspended.example")
        assert client.get(url).status_code == 200

        tenant.refresh_from_db()
        tenant.is_active = False
        tenant.save()

        assert client.get(url).status_code == 404

    def test_a_sibling_domain_changes_the_primary_payload(
        self, tenant_factory, deletes_are_lost
    ):
        """``assetsDomain`` prefers an explicit prefixed sibling row, so a
        new sibling changes what the PRIMARY domain resolves to
        (staging 2026-08-19)."""
        tenant = tenant_factory("stale-sibling")
        TenantDomain.objects.create(
            tenant=tenant, domain="stale-sibling.example", is_primary=True
        )
        client = Client()
        url = RESOLVE_URL.format("stale-sibling.example")
        assert client.get(url).json()["assetsDomain"] == ""

        TenantDomain.objects.create(
            tenant=tenant,
            domain="assets.stale-sibling.example",
            is_primary=False,
        )

        assert (
            client.get(url).json()["assetsDomain"]
            == "assets.stale-sibling.example"
        )

    def test_a_removed_domain_is_no_longer_trusted(
        self, tenant_factory, deletes_are_lost
    ):
        from tenant.middleware import tenant_domain_set

        tenant = tenant_factory("stale-origin")
        TenantDomain.objects.create(
            tenant=tenant, domain="stale-origin.example", is_primary=True
        )
        removed = TenantDomain.objects.create(
            tenant=tenant, domain="old.stale-origin.example", is_primary=False
        )
        tenant.refresh_from_db()
        assert "old.stale-origin.example" in tenant_domain_set(tenant)

        removed.delete()
        # The next request's TenantMainMiddleware loads the row afresh.
        tenant.refresh_from_db()

        assert tenant_domain_set(tenant) == {"stale-origin.example"}

    def test_a_reader_that_raced_the_write_cannot_poison_the_new_key(
        self, tenant_factory
    ):
        """A reader that loaded the row BEFORE the commit and stored its
        payload AFTER it writes under the old generation, which nobody
        reads any more — the race a delete-on-commit never closed."""
        tenant = tenant_factory("stale-race")
        TenantDomain.objects.create(
            tenant=tenant, domain="stale-race.example", is_primary=True
        )
        tenant.refresh_from_db()
        racing_reader_key = tenant_resolve_key("stale-race.example", tenant)

        tenant.store_name = "Committed"
        tenant.save()
        cache.set(racing_reader_key, {"storeName": "Pre-commit"}, 3600)

        response = Client().get(RESOLVE_URL.format("stale-race.example"))
        assert response.json()["storeName"] == "Committed"


@pytest.mark.django_db
class TestReadsStayAvailableWhileRedisIsDown:
    """Resolve runs on every storefront request, so an outage must cost
    the cache, never the answer."""

    def test_resolve_answers_from_the_database(
        self, tenant_factory, redis_down
    ):
        tenant = tenant_factory("down-resolve")
        TenantDomain.objects.create(
            tenant=tenant, domain="down-resolve.example", is_primary=True
        )
        url = RESOLVE_URL.format("down-resolve.example")

        with patch("tenant.views.cache", redis_down):
            first = Client().get(url)
            tenant.refresh_from_db()
            tenant.store_name = "Renamed while Redis is down"
            tenant.save()
            second = Client().get(url)

        assert first.status_code == 200
        assert first.json()["schemaName"] == tenant.schema_name
        assert second.json()["storeName"] == "Renamed while Redis is down"

    def test_the_origin_check_answers_from_the_database(
        self, tenant_factory, redis_down
    ):
        from tenant.middleware import origin_belongs_to_tenant

        tenant = tenant_factory("down-origin")
        TenantDomain.objects.create(
            tenant=tenant, domain="down-origin.example", is_primary=True
        )
        tenant.refresh_from_db()

        with patch("tenant.middleware.cache", redis_down):
            assert origin_belongs_to_tenant(
                tenant, "https://down-origin.example"
            )
            assert not origin_belongs_to_tenant(tenant, "https://evil.example")


#: Stands in for a ``Tenant``: the key helpers read ``pk``,
#: ``cache_generation`` and ``schema_name``.
SHOP = SimpleNamespace(pk=1, cache_generation=7, schema_name="shop")


class TestResolveKeyIsShapeVersioned:
    """The cached tenant-resolve value is the SERIALIZED payload, so a
    release that adds a field to ``TenantConfigSerializer`` would keep
    serving the pre-release shape for the whole TTL. The storefront
    validates that response against a generated Zod schema where the new
    field is REQUIRED, so every resolve fails and the tenant middleware
    turns it into a 404 "Store not found" sitewide.

    Adding ``b2bEnabled`` did exactly that to production on 2026-08-31.
    Keying by payload shape means the old entry is simply never read.
    """

    def test_added_serializer_field_changes_the_key(self):
        from rest_framework import serializers

        from tenant.cache import _tenant_config_shape
        from tenant.serializers import TenantConfigSerializer

        _tenant_config_shape.cache_clear()
        before = tenant_resolve_key("shop.example", SHOP)

        real_get_fields = TenantConfigSerializer.get_fields

        def with_extra_field(self):
            fields = real_get_fields(self)
            fields["brandNewFlag"] = serializers.BooleanField(read_only=True)
            return fields

        _tenant_config_shape.cache_clear()
        try:
            with patch.object(
                TenantConfigSerializer, "get_fields", with_extra_field
            ):
                after = tenant_resolve_key("shop.example", SHOP)
        finally:
            _tenant_config_shape.cache_clear()

        assert after != before, (
            "a new serializer field must not reuse the old cache key"
        )

    def test_key_is_stable_for_an_unchanged_shape(self):
        """Otherwise every process would miss the cache permanently."""
        assert tenant_resolve_key("shop.example", SHOP) == tenant_resolve_key(
            "shop.example", SHOP
        )

    def test_key_still_carries_the_domain_and_global_prefix(self):
        """Ops scan/delete these by domain (``*tenant_resolve*``), and the
        ``global:`` prefix is what makes the key schema-independent."""
        key = tenant_resolve_key("shop.example", SHOP)
        assert key.startswith(f"{GLOBAL_CACHE_PREFIX}tenant_resolve:")
        assert key.endswith(":shop.example")

    def test_the_generation_and_the_tenant_are_in_the_key(self):
        """A bump moves every reader to a new key; the pk keeps a domain
        that moved to another tenant off an entry cached for the first
        one at the same counter value."""
        key = tenant_resolve_key("shop.example", SHOP)
        bumped = SimpleNamespace(
            pk=SHOP.pk, cache_generation=8, schema_name=SHOP.schema_name
        )
        other_tenant = SimpleNamespace(pk=99, cache_generation=7)
        assert tenant_resolve_key("shop.example", bumped) != key
        assert tenant_resolve_key("shop.example", other_tenant) != key
        assert tenant_domains_key(bumped) != tenant_domains_key(SHOP)


class TestReceiverRegistration:
    def test_reimporting_the_signals_module_does_not_double_register(self):
        """Every receiver carries a dispatch_uid, so autoreload or a
        second import cannot make the purge run twice per save."""
        import importlib

        from django.db.models.signals import post_save

        from tenant import signals
        from tenant.models import Tenant

        before = len(post_save._live_receivers(Tenant))
        importlib.reload(signals)
        assert len(post_save._live_receivers(Tenant)) == before
