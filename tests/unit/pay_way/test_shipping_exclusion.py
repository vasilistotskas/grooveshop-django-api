"""Unit tests for the ``PayWayShippingExclusion`` model + the
admin-configurable exclusion layer in ``PayWayService.filter_by_carrier``.

The two-layer filter contract is the load-bearing part:

* **Layer 1** — DB exclusions (this module).
* **Layer 2** — carrier-specific hard constraints (covered by each
  carrier's own test suite).

If Layer 1's behavior breaks, the admin loses the ability to disable
pay-ways per shipping method at runtime. Re-add coverage in this file
before changing the service.
"""

from __future__ import annotations

from django.db import IntegrityError
from django.test import TestCase

from pay_way.factories import PayWayFactory, PayWayShippingExclusionFactory
from pay_way.models import PayWay, PayWayShippingExclusion
from pay_way.services import PayWayService
from shipping.enum import ShippingKind
from shipping.factories import ShippingProviderFactory


class PayWayShippingExclusionModelTests(TestCase):
    def test_unique_triple_constraint_blocks_duplicates(self):
        pay_way = PayWayFactory()
        provider = ShippingProviderFactory(code="acs")
        PayWayShippingExclusion.objects.create(
            pay_way=pay_way,
            shipping_provider=provider,
            shipping_kind=ShippingKind.PICKUP_POINT.value,
        )

        # Same (pay_way, provider, kind) must violate the unique
        # constraint — admins can't accidentally double-block.
        with self.assertRaises(IntegrityError):
            PayWayShippingExclusion.objects.create(
                pay_way=pay_way,
                shipping_provider=provider,
                shipping_kind=ShippingKind.PICKUP_POINT.value,
            )

    def test_same_pay_way_different_kind_is_allowed(self):
        pay_way = PayWayFactory()
        provider = ShippingProviderFactory(code="acs")
        a = PayWayShippingExclusion.objects.create(
            pay_way=pay_way,
            shipping_provider=provider,
            shipping_kind=ShippingKind.PICKUP_POINT.value,
        )
        b = PayWayShippingExclusion.objects.create(
            pay_way=pay_way,
            shipping_provider=provider,
            shipping_kind=ShippingKind.HOME_DELIVERY.value,
        )
        self.assertNotEqual(a.pk, b.pk)

    def test_str_includes_provider_code_and_kind(self):
        pay_way = PayWayFactory()
        provider = ShippingProviderFactory(code="boxnow")
        exclusion = PayWayShippingExclusion.objects.create(
            pay_way=pay_way,
            shipping_provider=provider,
            shipping_kind=ShippingKind.PICKUP_POINT.value,
        )
        rendered = str(exclusion)
        self.assertIn("boxnow", rendered)
        self.assertIn("pickup_point", rendered)


class FilterByCarrierExclusionTests(TestCase):
    """Layer 1 of ``PayWayService.filter_by_carrier``.

    Defers to the registered carrier hook (Layer 2) for code-level
    vetoes — that path is covered by the carrier's own tests. The
    cases here pin Layer 1's admin-configurable behaviour.
    """

    def setUp(self):
        self.online_pay_way = PayWayFactory(
            active=True,
            is_online_payment=True,
            requires_confirmation=False,
        )
        self.cod_pay_way = PayWayFactory(
            active=True,
            is_online_payment=False,
            requires_confirmation=False,
        )

    def _all(self):
        return PayWay.objects.all()

    def test_empty_table_returns_full_queryset(self):
        # Default deployment: no exclusion rows = every pay-way is
        # offered on every (provider, kind) the caller asks for.
        # Locks in the "no seed migration needed" guarantee.
        result = PayWayService.filter_by_carrier(
            self._all(),
            provider_code="acs",
            shipping_kind=ShippingKind.HOME_DELIVERY.value,
        )
        ids = set(result.values_list("id", flat=True))
        self.assertIn(self.online_pay_way.id, ids)
        self.assertIn(self.cod_pay_way.id, ids)

    def test_exclusion_for_matching_combo_filters_pay_way_out(self):
        provider = ShippingProviderFactory(code="boxnow")
        PayWayShippingExclusionFactory(
            pay_way=self.cod_pay_way,
            shipping_provider=provider,
            shipping_kind=ShippingKind.PICKUP_POINT.value,
        )

        result = PayWayService.filter_by_carrier(
            self._all(),
            provider_code="boxnow",
            shipping_kind=ShippingKind.PICKUP_POINT.value,
        )
        ids = set(result.values_list("id", flat=True))
        self.assertNotIn(self.cod_pay_way.id, ids)
        self.assertIn(self.online_pay_way.id, ids)

    def test_exclusion_isolated_to_its_kind(self):
        provider = ShippingProviderFactory(code="boxnow")
        PayWayShippingExclusionFactory(
            pay_way=self.cod_pay_way,
            shipping_provider=provider,
            shipping_kind=ShippingKind.PICKUP_POINT.value,
        )

        # Same pay-way, same provider, different kind — must NOT be
        # filtered out. This is the whole point of having a per-kind
        # exclusion row instead of a per-(provider, pay-way) one.
        result = PayWayService.filter_by_carrier(
            self._all(),
            provider_code="boxnow",
            shipping_kind=ShippingKind.HOME_DELIVERY.value,
        )
        ids = set(result.values_list("id", flat=True))
        self.assertIn(self.cod_pay_way.id, ids)

    def test_exclusion_isolated_to_its_provider(self):
        boxnow = ShippingProviderFactory(code="boxnow")
        ShippingProviderFactory(code="acs")
        PayWayShippingExclusionFactory(
            pay_way=self.cod_pay_way,
            shipping_provider=boxnow,
            shipping_kind=ShippingKind.PICKUP_POINT.value,
        )

        result = PayWayService.filter_by_carrier(
            self._all(),
            provider_code="acs",
            shipping_kind=ShippingKind.PICKUP_POINT.value,
        )
        ids = set(result.values_list("id", flat=True))
        self.assertIn(self.cod_pay_way.id, ids)

    def test_missing_inputs_short_circuit_to_pass_through(self):
        # When the caller doesn't supply both inputs the service has
        # no basis to filter; preserves the "wider query wins"
        # contract every existing caller relies on (e.g. the bare
        # ``/api/v1/pay_way`` list endpoint).
        for provider_code, shipping_kind in (
            (None, ShippingKind.HOME_DELIVERY.value),
            ("acs", None),
            ("", ""),
            (None, None),
        ):
            with self.subTest(provider=provider_code, kind=shipping_kind):
                result = PayWayService.filter_by_carrier(
                    self._all(),
                    provider_code=provider_code,
                    shipping_kind=shipping_kind,
                )
                ids = set(result.values_list("id", flat=True))
                self.assertIn(self.cod_pay_way.id, ids)
                self.assertIn(self.online_pay_way.id, ids)

    def test_unregistered_provider_short_circuits(self):
        # Caller-supplied provider_code that doesn't match any
        # registered adapter must not crash the filter — degrade to
        # pass-through so a typo in the storefront query doesn't
        # break the picker entirely.
        result = PayWayService.filter_by_carrier(
            self._all(),
            provider_code="not-a-real-carrier",
            shipping_kind=ShippingKind.HOME_DELIVERY.value,
        )
        ids = set(result.values_list("id", flat=True))
        self.assertIn(self.cod_pay_way.id, ids)
        self.assertIn(self.online_pay_way.id, ids)

    def test_unknown_kind_short_circuits(self):
        result = PayWayService.filter_by_carrier(
            self._all(),
            provider_code="acs",
            shipping_kind="not-a-real-kind",
        )
        ids = set(result.values_list("id", flat=True))
        self.assertIn(self.cod_pay_way.id, ids)
        self.assertIn(self.online_pay_way.id, ids)
