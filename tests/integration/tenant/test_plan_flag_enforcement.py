"""A paid-plan boundary must hold on the money path, not just at the door.

``tenant/permissions.py`` documents a two-tier contract: the PLAN flag
on the ``Tenant`` row (platform-controlled, "what this store has paid
for") AND the ``extra_settings`` toggle (merchant-controlled, "what the
store wants on right now"). Both must be true.

Only the first half was enforced, and only on each feature's own
endpoints. Order create never passes through those: ``create`` is a
public action and runs with ``permission_classes = []`` so guest
checkout works. Redemption and discounting therefore consulted only the
merchant-editable setting.

Reproducible bypass that closed: a merchant whose plan excludes gift
cards opens their own store admin (``extra_settings`` is in
``STORE_SHARED_APP_LABELS``, so ADMIN/OWNER hold ``change_setting``),
flips ``GIFT_CARDS_ENABLED``, issues a card — ``GiftCardService.issue``
has no enablement check — and a shopper redeems it at checkout. Same
shape for automatic promotions (which never touch the plan-gated coupon
endpoints) and for loyalty points (earned and redeemed through order
signals).

Folding the plan flag into each service's ``is_enabled()`` closes every
entry point at once, because all of them funnel through those methods.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from giftcard.services import GiftCardService
from loyalty.services import LoyaltyService
from promotion.services import PromotionEngine

# (service, plan field on Tenant, merchant setting key)
FEATURES = [
    (GiftCardService, "gift_cards_enabled", "GIFT_CARDS_ENABLED"),
    (PromotionEngine, "promotions_enabled", "PROMOTIONS_ENABLED"),
    (LoyaltyService, "loyalty_enabled", "LOYALTY_ENABLED"),
]


def _tenant(**flags):
    return SimpleNamespace(schema_name="acme", **flags)


@pytest.mark.parametrize("service,plan_field,setting_key", FEATURES)
class TestPlanFlagGatesTheMoneyPath:
    def test_merchant_setting_alone_does_not_enable(
        self, service, plan_field, setting_key
    ):
        """The bypass: plan says no, merchant flips the setting to yes."""
        with (
            patch(
                "tenant.membership.get_current_tenant",
                return_value=_tenant(**{plan_field: False}),
            ),
            patch(f"{service.__module__}.Setting.get", return_value=True),
        ):
            assert service.is_enabled() is False, (
                f"{service.__name__} honoured the merchant-editable "
                f"{setting_key} while the plan flag {plan_field} was off — "
                "a store can grant itself a feature it has not paid for"
            )

    def test_plan_alone_does_not_enable(self, service, plan_field, setting_key):
        """The merchant still decides whether it is switched on."""
        with (
            patch(
                "tenant.membership.get_current_tenant",
                return_value=_tenant(**{plan_field: True}),
            ),
            patch(f"{service.__module__}.Setting.get", return_value=False),
        ):
            assert service.is_enabled() is False

    def test_both_true_enables(self, service, plan_field, setting_key):
        with (
            patch(
                "tenant.membership.get_current_tenant",
                return_value=_tenant(**{plan_field: True}),
            ),
            patch(f"{service.__module__}.Setting.get", return_value=True),
        ):
            assert service.is_enabled() is True

    def test_public_schema_is_not_plan_gated(
        self, service, plan_field, setting_key
    ):
        """Platform routines run without a tenant and must not be blocked."""
        with (
            patch("tenant.membership.get_current_tenant", return_value=None),
            patch(f"{service.__module__}.Setting.get", return_value=True),
        ):
            assert service.is_enabled() is True

    def test_fake_tenant_fails_open(self, service, plan_field, setting_key):
        """``schema_context`` attaches a FakeTenant with no plan fields.

        A background task must not start refusing legitimate work
        because of how its schema was entered; the path that matters is
        HTTP, where a real Tenant row is attached.
        """
        with (
            patch(
                "tenant.membership.get_current_tenant",
                return_value=SimpleNamespace(schema_name="acme"),
            ),
            patch(f"{service.__module__}.Setting.get", return_value=True),
        ):
            assert service.is_enabled() is True


class TestPlanHelper:
    def test_absent_attribute_fails_open(self):
        from tenant.membership import tenant_plan_allows

        with patch(
            "tenant.membership.get_current_tenant",
            return_value=SimpleNamespace(schema_name="acme"),
        ):
            assert tenant_plan_allows("gift_cards_enabled") is True

    def test_no_tenant_allows(self):
        from tenant.membership import tenant_plan_allows

        with patch("tenant.membership.get_current_tenant", return_value=None):
            assert tenant_plan_allows("gift_cards_enabled") is True


class TestMerchantCannotSetDivergentFields:
    """Fields whose merchant-editability was itself the defect."""

    def test_default_currency_is_not_merchant_editable(self):
        """Display currency vs charge currency must not diverge.

        The storefront formats every price in ``defaultCurrency`` while
        every backend money path uses ``settings.DEFAULT_CURRENCY``, so
        a merchant setting "USD" would show $ prices while Django
        charged and invoiced EUR.
        """
        from tenant.role_scopes import TENANT_SELF_EDITABLE_FIELDS

        assert "default_currency" not in TENANT_SELF_EDITABLE_FIELDS

    def test_from_email_help_text_does_not_promise_it_works(self):
        from tenant.models import Tenant

        help_text = str(Tenant._meta.get_field("from_email").help_text)
        assert "NOT" in help_text or "not currently used" in help_text.lower()
