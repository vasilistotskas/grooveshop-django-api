from decimal import Decimal

import factory
from django.conf import settings
from djmoney.money import Money

from order.enum.payment_transaction import PaymentTransactionKind
from order.models.payment_transaction import PaymentTransaction
from order.payment import StripePaymentProvider


class PaymentTransactionFactory(factory.django.DjangoModelFactory):
    """A CHARGE of 50.00 by Stripe unless told otherwise.

    A refund names the charge it reverses (``charge=``), which the
    database requires; ``provider`` follows it. ``RefundTransactionFactory``
    does that for a test that only needs a refund row to exist.
    """

    order = factory.SubFactory(
        "order.factories.order.OrderFactory", num_order_items=0
    )
    kind = PaymentTransactionKind.CHARGE
    amount = Money(Decimal("50.00"), settings.DEFAULT_CURRENCY)
    provider = StripePaymentProvider.code
    provider_reference = factory.Sequence(lambda n: f"pi_ledger_{n}")

    class Meta:
        model = PaymentTransaction


class RefundTransactionFactory(PaymentTransactionFactory):
    """A REFUND of 10.00 against a 50.00 charge of the same order, which it
    creates unless ``charge=`` is given. The row alone: the order's
    ``payment_status`` is not touched (``PaymentLedger.record_refund`` does
    that)."""

    kind = PaymentTransactionKind.REFUND
    amount = Money(Decimal("10.00"), settings.DEFAULT_CURRENCY)
    provider_reference = factory.Sequence(lambda n: f"re_ledger_{n}")
    charge = factory.SubFactory(
        PaymentTransactionFactory, order=factory.SelfAttribute("..order")
    )
