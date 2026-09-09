"""A queryset ``.update()`` leaves parler serving the old label.

Migration 0022 renamed BOX NOW PAY ON THE GO with ``.update()``, which
writes past the shared cache ``PARLER_ENABLE_CACHING`` populates —
parler invalidates on ``save()``, through signals ``.update()`` never
fires. Measured on staging right after the v3.48.3 sync: two of three
tenants served ``PAY_ON_DELIVERY`` while the column already held
``BOX_NOW_PAY_ON_THE_GO``.

These tests pin the mechanism and the repair. The first one is the
important one — it fails if you delete the purge, because it reproduces
the staleness rather than asserting the migration ran.
"""

from __future__ import annotations

from importlib import import_module

from django.core.cache import cache
from django.test import TestCase, override_settings
from parler.cache import get_object_cache_keys

from pay_way.enum.pay_way import PayWayEnum
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory

# importlib because the module name starts with a digit, so it cannot
# be reached with a plain ``from ... import``.
_migration = import_module(
    "pay_way.migrations.0023_purge_stale_potg_translation_cache"
)
purge = _migration.purge_potg_translation_cache
PROVIDER_CODE = _migration.PROVIDER_CODE


class _SchemaEditor:
    """The two attributes ``RunPython`` code actually reads."""

    class connection:
        alias = "default"


@override_settings(PARLER_ENABLE_CACHING=True)
class PotgCachePurgeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.pay_way = PayWayFactory(
            active=True,
            provider_code=PROVIDER_CODE,
            settlement=PaySettlement.CARRIER_TERMINAL.value,
        )
        self.pay_way.translations.update(name=PayWayEnum.PAY_ON_DELIVERY.value)
        cache.clear()

    def _warm_the_cache(self):
        """Read once, the way a request does, so parler caches it."""
        return self.pay_way.safe_translation_getter("name", any_language=True)

    def test_it_evicts_the_label_a_queryset_update_left_behind(self):
        """The regression, end to end.

        Warm the cache, rename the way migration 0022 does, then show
        the old label is still being served — and that the purge is
        what fixes it.
        """
        assert self._warm_the_cache() == PayWayEnum.PAY_ON_DELIVERY.value

        self.pay_way.translations.update(
            name=PayWayEnum.BOX_NOW_PAY_ON_THE_GO.value
        )

        stale = type(self.pay_way).objects.get(pk=self.pay_way.pk)
        assert (
            stale.safe_translation_getter("name", any_language=True)
            == PayWayEnum.PAY_ON_DELIVERY.value
        ), "expected the stale cache to still serve the old name"

        purge(None, _SchemaEditor())

        fresh = type(self.pay_way).objects.get(pk=self.pay_way.pk)
        assert (
            fresh.safe_translation_getter("name", any_language=True)
            == PayWayEnum.BOX_NOW_PAY_ON_THE_GO.value
        )

    def test_it_leaves_other_pay_ways_alone(self):
        """Scoped to the rows 0022 touched. A blanket purge would work
        but would quietly hide a different stale-label bug."""
        other = PayWayFactory(
            active=True,
            provider_code="cash_on_delivery",
            settlement=PaySettlement.COURIER_CASH.value,
        )
        other.translations.update(name=PayWayEnum.PAY_ON_DELIVERY.value)
        cache.clear()
        other.safe_translation_getter("name", any_language=True)

        keys = get_object_cache_keys(other)
        assert any(cache.get(k) is not None for k in keys), (
            "the unrelated pay way should be cached before the purge"
        )

        purge(None, _SchemaEditor())

        assert any(cache.get(k) is not None for k in keys), (
            "the purge must not evict pay ways migration 0022 never wrote"
        )

    def test_a_cache_failure_does_not_fail_the_deploy(self):
        """The column is already correct; the entry expires on its own.
        Blocking a release on a cache eviction would be worse than the
        stale label it prevents."""
        with self.settings(CACHES={"default": {"BACKEND": "nonsense.Backend"}}):
            purge(None, _SchemaEditor())  # must not raise
