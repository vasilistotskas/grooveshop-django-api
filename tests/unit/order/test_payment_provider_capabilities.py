"""A payment flow is chosen by what a provider can do, never by its name.

Before this, five separate facts about providers were written as twelve
string literals spread across `cart/views`, `order/views`,
`order/services`, `pay_way/services` and `giftcard/*`:

    if pay_way.provider_code != "stripe": ...
    redirect_checkout_providers = {"viva_wallet"}
    supported_providers = {"stripe", "viva_wallet"}
    if code == "stripe": return bool(stripe_credentials()[...])

Each was right for two providers and silently wrong for a third —
nothing raised, the other branch was simply taken. These tests pin the
capability declarations and, more importantly, that the gates ask for a
capability rather than compare a vendor name.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from order.payment import (
    PaymentProvider,
    StripePaymentProvider,
    VivaWalletPaymentProvider,
    get_payment_provider_class,
    provider_supports,
    registered_provider_codes,
)

CAPABILITIES = (
    "supports_payment_intent",
    "supports_hosted_checkout",
    "supports_delegated_payment",
)


class TestTheRegistryIsTheOnlyList:
    def test_every_registered_class_declares_its_own_code(self):
        for code in registered_provider_codes():
            assert get_payment_provider_class(code).code == code

    def test_lookup_does_not_construct(self):
        """Capability gates run for tenants holding no credentials, and
        every constructor raises exactly then. A gate that instantiated
        would answer 500 where it means 400."""
        with patch.object(
            StripePaymentProvider,
            "__init__",
            side_effect=AssertionError("must not construct"),
        ):
            assert get_payment_provider_class("stripe") is StripePaymentProvider
            assert provider_supports("stripe", "supports_payment_intent")

    def test_an_unknown_provider_has_no_capabilities(self):
        """Fails closed. Every caller is a gate, so an unrecognised code
        must be treated as incapable rather than assumed able."""
        for capability in CAPABILITIES:
            assert not provider_supports("paypal", capability)
            assert not provider_supports("", capability)
            assert not provider_supports(None, capability)

    def test_capability_names_are_real_attributes(self):
        """``provider_supports`` reads by name, so a typo would answer
        False forever and silently disable a flow."""
        for capability in CAPABILITIES:
            assert hasattr(PaymentProvider, capability)


class TestWhatEachProviderCanDo:
    def test_stripe_does_both_flows(self):
        assert StripePaymentProvider.supports_payment_intent
        assert StripePaymentProvider.supports_hosted_checkout
        # Shared Payment Token — how ACP agentic checkout pays.
        assert StripePaymentProvider.supports_delegated_payment

    def test_viva_is_hosted_redirect_only(self):
        """Its ``process_payment`` delegates straight to
        ``create_checkout_session`` — there is no intent to confirm, so
        a Viva order is created BEFORE any money moves."""
        assert not VivaWalletPaymentProvider.supports_payment_intent
        assert VivaWalletPaymentProvider.supports_hosted_checkout
        assert not VivaWalletPaymentProvider.supports_delegated_payment

    def test_a_provider_declaring_delegated_payment_implements_it(self):
        for code in registered_provider_codes():
            provider_class = get_payment_provider_class(code)
            if provider_class.supports_delegated_payment:
                assert hasattr(provider_class, "confirm_delegated_payment"), (
                    f"{code} declares supports_delegated_payment but has no "
                    "confirm_delegated_payment"
                )

    def test_every_provider_can_do_at_least_one_flow(self):
        """A registered PSP that can do neither is unreachable — it
        would pass the credential gate and then be refused by every
        capability gate, with no explanation."""
        for code in registered_provider_codes():
            provider_class = get_payment_provider_class(code)
            assert (
                provider_class.supports_payment_intent
                or provider_class.supports_hosted_checkout
            ), f"{code} declares no usable checkout flow"


class TestProvidersOwnTheirShape:
    def test_stripe_asks_for_shipping_on_the_hosted_page(self):
        """Was ``if provider_code == "stripe"`` in the view."""

        class _Order:
            shipping_price = "3.50"

        params = StripePaymentProvider.checkout_session_params(
            StripePaymentProvider.__new__(StripePaymentProvider), _Order()
        )
        assert params == {"shipping_price": "3.50"}

    def test_viva_accumulates_every_issued_order_code(self):
        """The shopper may pay on ANY session Viva issued, and both the
        webhook and the return endpoint resolve the order from this
        list. Replacing the previous code would strand a payment."""
        provider = VivaWalletPaymentProvider.__new__(VivaWalletPaymentProvider)
        metadata: dict = {}

        provider.record_checkout_session(metadata, "code-1")
        provider.record_checkout_session(metadata, "code-2")
        provider.record_checkout_session(metadata, "code-1")

        assert metadata["viva_order_codes"] == ["code-1", "code-2"]

    def test_stripe_keeps_only_the_live_session(self):
        provider = StripePaymentProvider.__new__(StripePaymentProvider)
        metadata: dict = {}

        provider.record_checkout_session(metadata, "cs_1")
        provider.record_checkout_session(metadata, "cs_2")

        assert metadata["stripe_checkout_session_id"] == "cs_2"

    def test_the_base_hooks_are_inert(self):
        """A new PSP that needs neither must not have to override."""

        class _Bare(PaymentProvider):
            code = "bare"

            def process_payment(self, amount, order_id, **kwargs): ...
            def refund_payment(self, payment_id, amount=None): ...
            def get_payment_status(self, payment_id): ...
            def create_checkout_session(self, amount, order_id, **kwargs): ...

        metadata: dict = {}
        provider = _Bare()
        assert provider.checkout_session_params(object()) == {}
        provider.record_checkout_session(metadata, "x")
        assert metadata == {}
        assert _Bare.is_configured_for_tenant() is True


@pytest.mark.django_db
class TestCredentialChecksBelongToTheProvider:
    def test_configured_delegates_to_the_provider(self):
        from pay_way.services import PayWayService

        with patch.object(
            StripePaymentProvider,
            "is_configured_for_tenant",
            return_value=False,
        ):
            assert not PayWayService.is_provider_configured("stripe")
        with patch.object(
            StripePaymentProvider, "is_configured_for_tenant", return_value=True
        ):
            assert PayWayService.is_provider_configured("stripe")

    def test_an_unregistered_code_needs_no_credentials(self):
        """``""`` and offline processors are configured by definition;
        an online code with no adapter is caught by the hosted-checkout
        gate rather than silently hidden from the shopper here."""
        from pay_way.services import PayWayService

        assert PayWayService.is_provider_configured("cash_on_delivery")
        assert PayWayService.is_provider_configured("")
