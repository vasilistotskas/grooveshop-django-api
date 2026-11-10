from __future__ import annotations

import pytest

from order.signals import order_refunded


@pytest.fixture
def refunded_signals():
    """Every ``order_refunded`` the test caused, as its keyword arguments."""
    received: list[dict] = []

    def receiver(sender, **kwargs):
        received.append(kwargs)

    order_refunded.connect(receiver, weak=False)
    yield received
    order_refunded.disconnect(receiver)


@pytest.fixture
def refund_states():
    """Stripe's current state of each refund an event is built for
    (``tests.utils.stripe_refunds.refund_event``): the refund handlers read
    it with ``Refund.retrieve`` instead of trusting the event's snapshot.
    ``refund_states[refund_id] = "failed"`` changes what Stripe reports."""
    from types import SimpleNamespace
    from unittest import mock

    import stripe

    from tests.utils.stripe_refunds import (
        CREDENTIALS,
        CURRENT_REFUND_METADATA,
        CURRENT_REFUND_STATUS,
    )

    def retrieve(refund_id, **kwargs):
        if refund_id not in CURRENT_REFUND_STATUS:
            raise stripe.InvalidRequestError(
                f"No such refund: '{refund_id}'", "refund"
            )
        return SimpleNamespace(
            id=refund_id,
            status=CURRENT_REFUND_STATUS[refund_id],
            metadata=CURRENT_REFUND_METADATA.get(refund_id, {}),
        )

    CURRENT_REFUND_STATUS.clear()
    CURRENT_REFUND_METADATA.clear()
    with (
        mock.patch(
            "tenant.credentials.stripe_credentials", return_value=CREDENTIALS
        ),
        mock.patch(
            "order.payment.stripe.Refund.retrieve", side_effect=retrieve
        ),
    ):
        yield CURRENT_REFUND_STATUS
    CURRENT_REFUND_STATUS.clear()
    CURRENT_REFUND_METADATA.clear()
