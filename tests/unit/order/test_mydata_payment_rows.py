"""myDATA paymentMethodDetails rows for gift-card-settled orders.

AADE requires the row amounts to sum exactly to totalGrossValue, and a
gift-card settlement must never be reported as a POS capture that
never happened.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from djmoney.money import Money

from order.mydata.builder import _payment_rows, _pick_payment_type
from order.mydata.types import PAYMENT_METHOD_CASH, PAYMENT_METHOD_POS_CARD

pytestmark = pytest.mark.django_db


def _invoice_for(order, total: str):
    class _Invoice:
        pass

    invoice = _Invoice()
    invoice.order = order
    invoice.total = Money(Decimal(total), "EUR")
    return invoice


class TestPaymentRows:
    def test_pure_provider_payment_single_row(self):
        from order.factories.order import OrderFactory

        order = OrderFactory(payment_id="pi_123")
        invoice = _invoice_for(order, "50.00")

        rows = _payment_rows(invoice)

        assert rows == [(PAYMENT_METHOD_POS_CARD, Decimal("50.00"))]

    def test_full_gift_card_settlement_single_gc_row(self):
        from order.factories.order import OrderFactory

        order = OrderFactory()
        order.payment_id = f"GIFTCARD_{order.uuid}"
        order.gift_card_amount = Money(Decimal("50.00"), "EUR")
        order.save(
            update_fields=[
                "payment_id",
                "gift_card_amount",
                "gift_card_amount_currency",
            ]
        )
        invoice = _invoice_for(order, "50.00")

        rows = _payment_rows(invoice)

        assert rows == [(PAYMENT_METHOD_CASH, Decimal("50.00"))]

    def test_split_payment_two_rows_summing_to_total(self):
        from order.factories.order import OrderFactory

        order = OrderFactory(payment_id="pi_456")
        order.gift_card_amount = Money(Decimal("20.00"), "EUR")
        order.save(
            update_fields=["gift_card_amount", "gift_card_amount_currency"]
        )
        invoice = _invoice_for(order, "50.00")

        rows = _payment_rows(invoice)

        assert rows == [
            (PAYMENT_METHOD_CASH, Decimal("20.00")),
            (PAYMENT_METHOD_POS_CARD, Decimal("30.00")),
        ]
        assert sum(amount for _, amount in rows) == Decimal("50.00")

    def test_gift_card_payment_type_is_configurable(self):
        from order.factories.order import OrderFactory

        order = OrderFactory(payment_id="pi_789")
        order.gift_card_amount = Money(Decimal("10.00"), "EUR")
        order.save(
            update_fields=["gift_card_amount", "gift_card_amount_currency"]
        )
        invoice = _invoice_for(order, "50.00")

        def _get(key, default=None):
            return {"MYDATA_GIFT_CARD_PAYMENT_TYPE": 5}.get(key, default)

        with patch("extra_settings.models.Setting.get", side_effect=_get):
            rows = _payment_rows(invoice)

        assert rows[0] == (5, Decimal("10.00"))

    def test_invalid_configured_type_falls_back_to_cash(self):
        from order.factories.order import OrderFactory

        order = OrderFactory(payment_id="pi_000")
        order.gift_card_amount = Money(Decimal("10.00"), "EUR")
        order.save(
            update_fields=["gift_card_amount", "gift_card_amount_currency"]
        )
        invoice = _invoice_for(order, "50.00")

        def _get(key, default=None):
            return {"MYDATA_GIFT_CARD_PAYMENT_TYPE": 99}.get(key, default)

        with patch("extra_settings.models.Setting.get", side_effect=_get):
            rows = _payment_rows(invoice)

        assert rows[0][0] == PAYMENT_METHOD_CASH

    def test_giftcard_payment_id_never_maps_to_pos(self):
        from order.factories.order import OrderFactory

        order = OrderFactory()
        order.payment_id = f"GIFTCARD_{order.uuid}"
        order.save(update_fields=["payment_id"])
        invoice = _invoice_for(order, "50.00")

        assert _pick_payment_type(invoice) != PAYMENT_METHOD_POS_CARD
