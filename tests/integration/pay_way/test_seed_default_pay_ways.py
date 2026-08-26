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

from pay_way.models import PayWay

MIGRATION = "pay_way.migrations.0019_seed_default_pay_ways"


@pytest.fixture
def seed():
    module = importlib.import_module(MIGRATION)

    class _SchemaEditor:
        class connection:
            alias = "default"

    def _run():
        from django.apps import apps

        module.seed_pay_ways(apps, _SchemaEditor)

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

        assert by_code["cash_on_delivery"].is_online_payment is False
        assert by_code["viva_wallet"].is_online_payment is True
        assert by_code["stripe"].is_online_payment is True

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

        online = PayWay.objects.filter(is_online_payment=True)
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
            is_online_payment=True,
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
            is_online_payment=False,
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
            is_online_payment=False,
        )

        seed()

        assert PayWay.objects.count() == 3
        assert (
            PayWay.objects.filter(provider_code="cash_on_delivery").count() == 1
        )
