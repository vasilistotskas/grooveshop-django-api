"""What Stripe charged for a payment, read from its balance transaction.

A Stripe fee lives on the charge's balance transaction (``fee`` in the
smallest unit of the settlement currency), which dj-stripe syncs along with
the charge. It reaches the payment's ledger row (``PaymentTransaction.fee``)
from two places, because Stripe orders its events freely:

- the charge's own events (``charge.succeeded``; ``charge.updated``, which
  is when a slower payment method attaches its balance transaction), when
  the ledger already holds the payment;
- the ``payment_intent.succeeded`` that books the payment, when the charge's
  events came first and found nothing to write to.

A fee that cannot be read yet, or is in another currency than the payment,
is left unrecorded: "not reported", never zero and never converted. The
Viva counterpart is ``order.views.viva_webhook`` (event 1799).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import stripe
from djstripe.models import Charge

from order.enum.payment_transaction import PaymentFeeSource
from order.models.payment_transaction import PaymentTransaction
from order.payment import StripePaymentProvider, money_from_minor_units
from order.payment_ledger import PaymentLedger
from tenant.credentials import stripe_credentials

logger = logging.getLogger(__name__)


def record_stripe_charge_fee(
    charge: Mapping[str, Any],
) -> PaymentTransaction | None:
    """Write the fee of the Stripe *charge* (its API object) onto the ledger
    row of the payment it belongs to; ``None`` when nothing was written.

    Reads nothing from Stripe unless the ledger holds the payment, so a
    charge that is none of this store's orders costs no API call.
    """
    intent_id = charge.get("payment_intent")
    if not intent_id or not charge.get("balance_transaction"):
        return None
    if not PaymentTransaction.objects.filter(
        provider=StripePaymentProvider.code, provider_reference=intent_id
    ).exists():
        return None

    synced = Charge.sync_from_stripe_data(
        charge, api_key=stripe_credentials()["secret_key"]
    )
    balance_transaction = synced.balance_transaction
    if balance_transaction is None or balance_transaction.fee is None:
        return None
    return PaymentLedger.record_fee(
        provider=StripePaymentProvider.code,
        reference=intent_id,
        fee=money_from_minor_units(
            balance_transaction.fee, balance_transaction.currency
        ),
        source=PaymentFeeSource.STRIPE_BALANCE_TRANSACTION,
    )


def record_stripe_fee_for_intent(
    payment_intent_id: str, charge_id: str | None
) -> None:
    """The fee of a payment that was just booked, for the case its charge's
    events were processed before the booking.

    A failed Stripe call is logged and swallowed: the payment is booked, and
    the fee still arrives with the charge's next ``charge.updated``.
    """
    if not charge_id:
        return
    awaiting_fee = PaymentTransaction.objects.filter(
        provider=StripePaymentProvider.code,
        provider_reference=payment_intent_id,
        fee_source="",
    ).exists()
    if not awaiting_fee:
        return
    try:
        # A dict: the API object is not a mapping, which dj-stripe's sync
        # (and ``record_stripe_charge_fee``) read it as.
        charge = stripe.Charge.retrieve(
            charge_id, api_key=stripe_credentials()["secret_key"]
        ).to_dict()
    except stripe.StripeError:
        logger.exception(
            "Could not read charge %s to record the fee of payment %s",
            charge_id,
            payment_intent_id,
        )
        return
    record_stripe_charge_fee(charge)
