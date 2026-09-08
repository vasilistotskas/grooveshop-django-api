"""A fresh tenant must be able to take an order; an existing one must not change.

``Order.pay_way`` is required at checkout and provisioning seeded no
payment methods, so a newly provisioned store could not accept a single
order until someone hand-created rows in the admin (the staging tenant
``aurora`` had zero). ``pay_way/migrations/0019_seed_default_pay_ways``
closes that.

The dangerous half is the other direction: these rows are LIVE on
existing tenants — production runs ``cash_on_delivery`` and
``viva_wallet`` ACTIVE — so a seeder written with ``update_or_create``
(as the shipping-provider precedent uses) would have reset a merchant's
working payment methods to the inactive defaults on the next deploy.
These tests pin both halves.
"""

from __future__ import annotations

import importlib

import pytest

from pay_way.enum.settlement import PaySettlement
from pay_way.models import PayWay

MIGRATION = "pay_way.migrations.0019_seed_default_pay_ways"
BACKFILL = (
    "pay_way.migrations."
    "0020_payway_settlement_alter_payway_is_online_payment_and_more"
)


@pytest.fixture
def seed():
    """Replay what a freshly provisioned tenant actually gets.

    That is 0019 (the rows) AND 0020's backfill (their settlement) —
    a fresh tenant runs both, in order, before serving a request.

    Replaying 0019 alone would be a lie about production and would also
    misfire here: these run against the CURRENT ``PayWay``, whose
    ``save()`` derives the deprecated booleans from ``settlement``. With
    no settlement set, every seeded row would come back claiming to be
    paid online — including cash-on-delivery.
    """
    seeder = importlib.import_module(MIGRATION)
    backfill = importlib.import_module(BACKFILL)

    class _SchemaEditor:
        class connection:
            alias = "default"

    def _run():
        from django.apps import apps

        seeder.seed_pay_ways(apps, _SchemaEditor)

        # Re-assert what the HISTORICAL model would have written.
        #
        # A real fresh tenant runs 0019 against the model as it stood
        # then: no ``settlement`` column and no custom ``save()``. Here
        # 0019 runs against the current model, whose ``save()`` derives
        # the booleans FROM ``settlement`` — which 0019 never sets, so
        # it lands on the field default and every seeded row comes back
        # claiming to be paid online. The backfill would then read those
        # corrupted booleans and agree with them.
        #
        # ``.update()`` writes straight to SQL, bypassing ``save()``,
        # which is exactly the historical behaviour we need before
        # exercising the backfill on realistic input.
        for code, _name, _active, is_online, _sort in seeder.DEFAULT_PAY_WAYS:
            PayWay.objects.filter(provider_code=code).update(
                is_online_payment=is_online,
                requires_confirmation=False,
                settlement="online",  # AddField's default, pre-backfill
            )

        backfill.backfill_settlement(apps, _SchemaEditor)

    return _run


@pytest.mark.django_db
class TestFreshTenantSeeding:
    def test_seeds_a_working_checkout(self, seed):
        PayWay.objects.all().delete()

        seed()

        codes = set(PayWay.objects.values_list("provider_code", flat=True))
        assert codes == {"cash_on_delivery", "viva_wallet", "stripe"}

    def test_offline_method_is_active_online_methods_are_not(self, seed):
        """An active card option with no credentials is a broken checkout.

        Viva and Stripe credentials live on the Tenant row and are empty
        for a new tenant, so they stay dark until an admin configures
        them. COD needs nothing, so it ships usable.
        """
        PayWay.objects.all().delete()

        seed()

        by_code = {p.provider_code: p for p in PayWay.objects.all()}
        assert by_code["cash_on_delivery"].active is True
        assert by_code["viva_wallet"].active is False
        assert by_code["stripe"].active is False

        # Settlement is the stored truth. COD is collected by the
        # courier at the door — NOT at a carrier terminal, which is
        # BoxNow PAY ON THE GO and is seeded separately in 0021.
        assert by_code["cash_on_delivery"].settlement == (
            PaySettlement.COURIER_CASH
        )
        assert by_code["viva_wallet"].settlement == PaySettlement.ONLINE
        assert by_code["stripe"].settlement == PaySettlement.ONLINE

    def test_online_codes_match_the_payment_provider_registry(self, seed):
        """A code the registry does not know 500s mid-checkout.

        ``ImproperlyConfigured`` is the EXPECTED outcome here and is
        itself the argument for seeding these inactive: the provider
        refuses to construct without tenant credentials. Only
        ``ValueError`` ("Unknown payment provider") means a bad code.
        """
        from django.core.exceptions import ImproperlyConfigured

        from order.payment import get_payment_provider

        PayWay.objects.all().delete()
        seed()

        online = PayWay.objects.filter(settlement=PaySettlement.ONLINE)
        assert online.count() == 2
        for pay_way in online:
            try:
                get_payment_provider(pay_way.provider_code)
            except ImproperlyConfigured:
                pass
            except ValueError as exc:  # pragma: no cover - failure path
                pytest.fail(
                    f"{pay_way.provider_code!r} is not registered in "
                    f"order.payment.get_payment_provider: {exc}"
                )

    def test_names_are_enum_keys_the_storefront_can_translate(self, seed):
        """The storefront renders ``payment_methods.<KEY>`` from its locale.

        Storing a display string here would surface a raw label that
        bypasses translation.
        """
        from pay_way.enum.pay_way import PayWayEnum

        PayWay.objects.all().delete()
        seed()

        valid = set(PayWayEnum.values)
        for pay_way in PayWay.objects.all():
            name = pay_way.safe_translation_getter(
                "name", language_code="el", any_language=False
            )
            assert name in valid, f"{pay_way.provider_code} -> {name!r}"


@pytest.mark.django_db
class TestExistingTenantIsUntouched:
    """The regression that would have broken production."""

    def test_does_not_deactivate_a_live_payment_method(self, seed):
        PayWay.objects.all().delete()
        live = PayWay.objects.create(
            provider_code="viva_wallet",
            active=True,
            settlement=PaySettlement.ONLINE,
        )
        live.set_current_language("el")
        live.name = "CREDIT_CARD"
        live.save()

        seed()

        live.refresh_from_db()
        assert live.active is True, (
            "seeding reset a merchant's LIVE payment method to the inactive "
            "default — use get_or_create, never update_or_create"
        )
        assert PayWay.objects.filter(provider_code="viva_wallet").count() == 1

    def test_preserves_merchant_edits_to_seeded_fields(self, seed):
        """A merchant who DISABLED cash on delivery must stay disabled."""
        PayWay.objects.all().delete()
        cod = PayWay.objects.create(
            provider_code="cash_on_delivery",
            active=False,
            settlement=PaySettlement.COURIER_CASH,
        )
        # .update() bypasses SortableModel.save(), which reassigns
        # sort_order on every create.
        PayWay.objects.filter(pk=cod.pk).update(sort_order=99)

        seed()

        cod.refresh_from_db()
        assert cod.active is False
        assert cod.sort_order == 99

    def test_is_idempotent(self, seed):
        PayWay.objects.all().delete()

        seed()
        seed()
        seed()

        assert PayWay.objects.count() == 3
        for code in ("cash_on_delivery", "viva_wallet", "stripe"):
            assert PayWay.objects.filter(provider_code=code).count() == 1

    def test_backfills_only_what_is_missing(self, seed):
        PayWay.objects.all().delete()
        PayWay.objects.create(
            provider_code="cash_on_delivery",
            active=True,
            settlement=PaySettlement.COURIER_CASH,
        )

        seed()

        assert PayWay.objects.count() == 3
        assert (
            PayWay.objects.filter(provider_code="cash_on_delivery").count() == 1
        )
