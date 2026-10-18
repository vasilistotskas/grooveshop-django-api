"""An in-memory Stripe for tests that need an intent to carry its metadata.

``StripePaymentProvider`` is exercised for real: ``create`` stores whatever
the provider sends (amount, currency, metadata), ``retrieve`` hands it back,
so what an intent was created FOR travels exactly as it does against
Stripe. Only the network edge (``stripe.PaymentIntent``), the dj-stripe sync
and the tenant credential read are replaced.
"""

from __future__ import annotations

from contextlib import contextmanager
from itertools import count
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch


class FakeStripe:
    def __init__(self) -> None:
        self.intents: dict[str, SimpleNamespace] = {}
        self._ids = count(1)

    def create(self, **params: Any) -> SimpleNamespace:
        intent_id = f"pi_fake_{next(self._ids)}"
        intent = SimpleNamespace(
            id=intent_id,
            status="requires_payment_method",
            client_secret=f"{intent_id}_secret",
            next_action=None,
            amount=params["amount"],
            amount_received=0,
            currency=params["currency"],
            metadata=dict(params.get("metadata") or {}),
            created=1_700_000_000,
        )
        self.intents[intent_id] = intent
        return intent

    def retrieve(self, intent_id: str, **_: Any) -> SimpleNamespace:
        return self.intents[intent_id]

    def pay(self, intent_id: str) -> SimpleNamespace:
        """The shopper confirms the intent and Stripe collects it."""
        intent = self.intents[intent_id]
        intent.status = "succeeded"
        intent.amount_received = intent.amount
        return intent


@contextmanager
def fake_stripe():
    stripe = FakeStripe()
    with (
        patch("order.payment.stripe.PaymentIntent.create", stripe.create),
        patch("order.payment.stripe.PaymentIntent.retrieve", stripe.retrieve),
        patch("order.payment.PaymentIntent.sync_from_stripe_data"),
        patch(
            "tenant.credentials.stripe_credentials",
            return_value={
                "secret_key": "sk_test_dummy_tenant_key",
                "publishable_key": "pk_test_dummy_tenant_key",
                "live_mode": False,
            },
        ),
    ):
        yield stripe
