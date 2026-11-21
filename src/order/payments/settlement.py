"""A payment a provider took whose webhook never landed, settled anyway.

The provider's webhook is what marks an online order paid, and it can be
lost. Before ``auto_cancel_stuck_pending_orders`` releases an unpaid order,
the provider is read, and a payment it took is settled through the code its
own webhook runs (``viva_settlement``, ``stripe_settlement``), so a charged
order is never canceled unpaid and the webhook that arrives later changes
nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from order.enum.landed_payment import LandedPayment
from order.payments import stripe_settlement, viva_settlement
from order.payments.payment import (
    StripePaymentProvider,
    VivaWalletPaymentProvider,
)

if TYPE_CHECKING:
    from order.models.order import Order

#: Each provider's settlement of a payment read back from it. One with no
#: entry has no payment a webhook settles.
_SETTLERS: dict[str, Callable[[Order], LandedPayment]] = {
    VivaWalletPaymentProvider.code: viva_settlement.settle_landed_payment,
    StripePaymentProvider.code: stripe_settlement.settle_landed_payment,
}


def settle_landed_payment(order: Order, provider_code: str) -> LandedPayment:
    """Settle the payment the provider *provider_code* took for the unpaid
    *order*, through the code its webhook runs, and say what the provider
    holds: only ``LandedPayment.NONE`` lets the order be canceled.

    Raises ``PaymentStatusUnavailableError`` when the provider could not be
    read: a payment may have landed, so the caller keeps the order.
    """
    settle = _SETTLERS.get(provider_code)
    return settle(order) if settle is not None else LandedPayment.NONE
