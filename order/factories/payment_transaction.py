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
    database requires; ``provider`` follows it.
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
