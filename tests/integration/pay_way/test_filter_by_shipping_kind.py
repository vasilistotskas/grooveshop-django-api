"""A shipping kind with no carrier named still constrains payment.

Checkout's ``home_delivery`` is provider-agnostic: the storefront has
no carrier code to send because Django picks the active home-delivery
provider at order-creation time, so the request arrives as
``?shippingKind=home_delivery`` alone. The filter used to read that as
"nothing to do" and return every pay-way — which put BoxNow PAY ON THE
GO, a locker-terminal product, on the courier home-delivery step.
Reproduced on staging 2026-09-09: three radio buttons where the owner's
requirement is that PAY ON THE GO appear *only* under BOX NOW.

The rule these tests pin is intersection, not union: a pay-way offered
without a named carrier must be settleable by EVERY carrier that could
end up serving the kind. Widening this to a union would put back a
narrower version of the same defect — an option that works only if
Django happens to pick the right carrier.
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase

from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from pay_way.models import PayWay, PayWayShippingExclusion
from pay_way.services import PayWayService
from shipping.enum import ShippingKind
from shipping.models import ShippingProvider


class FilterByShippingKindTests(TestCase):
    def setUp(self):
        # ``shipping/migrations/0002_seed_providers`` leaves ``acs`` and
        # ``boxnow`` rows in every test DB, INACTIVE, and the
        # ``_reseed_shipping_providers`` fixture that restores them is
        # keyed on the ``django_db`` marker a ``TestCase`` does not
        # carry. So each test states its own carrier landscape through
        # ``_activate`` rather than inheriting either state.
        self.courier_cash = PayWayFactory(
            active=True,
            provider_code="cash_on_delivery",
            settlement=PaySettlement.COURIER_CASH.value,
        )
        self.pay_on_the_go = PayWayFactory.create_carrier_terminal_payment()
        self.online = PayWayFactory.create_online_payment()
        self.bank_transfer = PayWayFactory.create_offline_payment()

    # -- helpers -----------------------------------------------------

    def _activate(self, code: str, **flags):
        """Force a provider row into the state this test needs.

        NOT ``ShippingProviderFactory``: its ``django_get_or_create``
        on ``code`` returns the row ``shipping/migrations/
        0002_seed_providers`` already created and silently DROPS the
        keyword flags, so ``is_active=True`` never lands. That made
        these tests pass serially against a test DB whose rows a
        ``transaction=True`` test had flushed, and fail under
        ``-n auto`` where the seeded rows are intact.
        """
        flags.setdefault("supports_home_delivery", code == "acs")
        flags.setdefault("supports_pickup_point", code == "boxnow")
        updated = ShippingProvider.objects.filter(code=code).update(
            is_active=True, **flags
        )
        if not updated:
            ShippingProvider.objects.create(
                code=code, name=code, is_active=True, **flags
            )
        return ShippingProvider.objects.get(code=code)

    @staticmethod
    def _configured(*, acs: bool = True, boxnow: bool = True):
        """Both carriers gate every kind on tenant credentials.

        ``is_kind_enabled`` returns False for an unconfigured tenant,
        which correctly drops that carrier from the candidate set — so
        without this the tests would assert against a landscape with no
        candidates at all and pass for the wrong reason.
        """
        from contextlib import ExitStack

        stack = ExitStack()
        stack.enter_context(
            patch("shipping_acs.config.is_configured", return_value=acs)
        )
        stack.enter_context(
            patch(
                "shipping_boxnow.services.is_configured",
                return_value=boxnow,
            )
        )
        return stack

    def _offered(self, kind: ShippingKind) -> set[int]:
        qs = PayWayService.filter_by_shipping_kind(
            PayWay.objects.all(),
            shipping_kind=kind.value,
        )
        return set(qs.values_list("id", flat=True))

    # -- one candidate carrier ---------------------------------------

    def test_home_delivery_does_not_offer_the_locker_product(self):
        """The staging defect, stated as money.

        ACS is the only home-delivery carrier. It hands parcels over at
        a door and can take cash there; it has no locker terminal, so
        money owed under PAY ON THE GO has nothing in the flow that
        could collect it.
        """
        self._activate("acs")
        with self._configured():
            offered = self._offered(ShippingKind.HOME_DELIVERY)

        self.assertNotIn(self.pay_on_the_go.id, offered)
        self.assertIn(self.courier_cash.id, offered)
        self.assertIn(self.online.id, offered)

    def test_locker_pickup_does_not_offer_courier_cash(self):
        """Mirror case: BoxNow is the only pickup-point carrier."""
        self._activate("boxnow")
        with self._configured():
            offered = self._offered(ShippingKind.PICKUP_POINT)

        self.assertNotIn(self.courier_cash.id, offered)
        self.assertIn(self.pay_on_the_go.id, offered)
        self.assertIn(self.online.id, offered)

    # -- two candidate carriers --------------------------------------

    def test_two_candidates_offer_only_what_both_can_settle(self):
        """ACS and BoxNow both do pickup points, differently.

        ACS lockers take courier cash and never a BoxNow terminal;
        BoxNow lockers are the exact opposite. Django picks the carrier
        after the shopper has chosen how to pay, so offering either
        collect-on-delivery product here is a coin flip on whether the
        money is collectable. Only what both accept may be shown.
        """
        self._activate("acs", supports_pickup_point=True)
        self._activate("boxnow", supports_pickup_point=True)
        with self._configured():
            # ACS Smartpoint also needs its own Setting; forced on here
            # so the landscape really is two candidates. The flag
            # itself is covered by its own test below.
            with patch(
                "shipping_acs.carrier.AcsCarrier.is_kind_enabled",
                return_value=True,
            ):
                offered = self._offered(ShippingKind.PICKUP_POINT)

        self.assertNotIn(self.courier_cash.id, offered)
        self.assertNotIn(self.pay_on_the_go.id, offered)
        # Both carriers can settle these two.
        self.assertIn(self.online.id, offered)
        self.assertIn(self.bank_transfer.id, offered)

    def test_an_exclusion_row_on_one_candidate_removes_the_pay_way(self):
        """Layer 1 intersects too.

        An operator who switches a pay-way off for ACS home delivery
        must not see it reappear because a second home-delivery carrier
        has no such row — they turned it off for a kind they cannot
        pin to a carrier from checkout.
        """
        self._activate("acs")
        acs = ShippingProvider.objects.get(code="acs")
        PayWayShippingExclusion.objects.create(
            pay_way=self.courier_cash,
            shipping_provider=acs,
            shipping_kind=ShippingKind.HOME_DELIVERY.value,
        )

        with self._configured():
            offered = self._offered(ShippingKind.HOME_DELIVERY)

        self.assertNotIn(self.courier_cash.id, offered)
        self.assertIn(self.online.id, offered)

    # -- degenerate inputs -------------------------------------------

    def test_no_active_carrier_for_the_kind_passes_through(self):
        """Nothing serves the kind, so there is no pairing to police.

        The shopper cannot reach a payment step for a shipping option
        that does not exist; narrowing to an empty list here would only
        hide a misconfiguration behind an empty payment step. Same
        short-circuit convention as ``filter_by_carrier``.
        """
        self.assertEqual(
            self._offered(ShippingKind.HOME_DELIVERY),
            set(PayWay.objects.values_list("id", flat=True)),
        )

    def test_a_kind_gated_off_by_its_carrier_is_not_a_candidate(self):
        """``is_kind_enabled`` is honoured, not just ``is_active``.

        ACS Smartpoint hides behind its own Setting. With the flag off
        ACS is not a pickup-point candidate at all, so its acceptance
        of courier cash must not loosen BoxNow's locker rule.
        """
        self._activate("acs", supports_pickup_point=True)
        self._activate("boxnow", supports_pickup_point=True)
        with self._configured():
            with patch(
                "shipping_acs.carrier.AcsCarrier.is_kind_enabled",
                return_value=False,
            ):
                offered = self._offered(ShippingKind.PICKUP_POINT)

        self.assertNotIn(self.courier_cash.id, offered)
        self.assertIn(self.pay_on_the_go.id, offered)

    def test_unknown_and_empty_kinds_pass_through(self):
        every_id = set(PayWay.objects.values_list("id", flat=True))
        for value in ("", None, "teleportation"):
            qs = PayWayService.filter_by_shipping_kind(
                PayWay.objects.all(),
                shipping_kind=value,
            )
            self.assertEqual(set(qs.values_list("id", flat=True)), every_id)
