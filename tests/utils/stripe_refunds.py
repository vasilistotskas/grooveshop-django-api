"""Stripe's refund endpoint, for tests that refund a payment.

``stripe.Refund.create`` is the one seam that is patched: the provider
builds its real request and reads a real-shaped refund back, so an
assertion on :attr:`FakeStripeRefunds.calls` inspects what would have gone
to Stripe. Idempotency follows Stripe's documented behaviour
(docs.stripe.com/api/idempotent_requests): the first answer for a key is
kept and returned for a repeat of the same request, and reusing a key with
different parameters is an ``IdempotencyError``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Self
from unittest import mock

import stripe

CREDENTIALS = {
    "secret_key": "sk_test_ledger",
    "publishable_key": "pk_test_ledger",
    "live_mode": False,
}


class FakeStripeRefunds:
    """A Stripe that refunds payment intents.

    ``status`` is what a new refund reports (``succeeded``, ``pending``,
    ``requires_action``, ``failed``, ``canceled``); ``fail_with`` makes the
    next calls raise that error instead.
    """

    def __init__(
        self,
        *,
        status: str = "succeeded",
        fail_with: Exception | None = None,
    ):
        self.status = status
        self.fail_with = fail_with
        #: Every call, as the keyword arguments Stripe received.
        self.calls: list[dict[str, Any]] = []
        #: The refunds Stripe actually made (a replayed key makes none).
        self.created: list[SimpleNamespace] = []
        self._by_key: dict[str, tuple[dict[str, Any], SimpleNamespace]] = {}
        self._patches = [
            mock.patch(
                "tenant.credentials.stripe_credentials",
                return_value=CREDENTIALS,
            ),
            mock.patch(
                "order.payment.stripe.Refund.create", side_effect=self._create
            ),
            mock.patch("order.payment.Refund.sync_from_stripe_data"),
        ]

    def __enter__(self) -> Self:
        for patch in self._patches:
            patch.start()
        return self

    def __exit__(self, *exc_info) -> None:
        for patch in reversed(self._patches):
            patch.stop()

    def _create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        if self.fail_with is not None:
            raise self.fail_with
        key = kwargs.get("idempotency_key")
        params = {
            name: value
            for name, value in kwargs.items()
            if name not in {"idempotency_key", "api_key"}
        }
        if key in self._by_key:
            saved_params, refund = self._by_key[key]
            if saved_params != params:
                raise stripe.IdempotencyError(
                    "Keys for idempotent requests can only be used with the "
                    "same parameters they were first used with."
                )
            return refund
        refund = SimpleNamespace(
            id=f"re_{len(self.created) + 1}",
            status=self.status,
            amount=params["amount"],
            currency="eur",
            payment_intent=params["payment_intent"],
        )
        self.created.append(refund)
        if key is not None:
            self._by_key[key] = (params, refund)
        return refund

    @property
    def keys(self) -> list[str]:
        """The idempotency key of every call, in order."""
        return [call["idempotency_key"] for call in self.calls]

    @property
    def amounts(self) -> list[int]:
        """The minor-unit amount of every call, in order."""
        return [call["amount"] for call in self.calls]
