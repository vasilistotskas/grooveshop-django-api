"""Purchase-at-creation policy for EVERY ``PaySettlement`` value.

Covers ``meta_capi/signals.py``: ``_on_order_created`` schedules Purchase
immediately for any settlement other than ``ONLINE`` (the provider webhook
covers online payments), and ``_on_order_refunded`` forwards the amount.
The existing ``test_signals.py`` pins COURIER_CASH, ONLINE and a missing
pay-way; this module pins the remaining members and guards the enum.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

import pytest

from meta_capi.signals import _on_order_created, _on_order_refunded
from pay_way.enum.settlement import PaySettlement

_EXPECT_PURCHASE_AT_CREATION = {
    PaySettlement.ONLINE: False,
    PaySettlement.COURIER_CASH: True,
    PaySettlement.CARRIER_TERMINAL: True,
    PaySettlement.OFFLINE_TRANSFER: True,
}


def test_policy_table_covers_every_settlement():
    # A new PaySettlement member must get an explicit decision here.
    assert set(_EXPECT_PURCHASE_AT_CREATION) == set(PaySettlement)


@pytest.mark.parametrize(
    ("settlement", "expect_purchase"),
    list(_EXPECT_PURCHASE_AT_CREATION.items()),
    ids=[s.value for s in _EXPECT_PURCHASE_AT_CREATION],
)
def test_order_created_schedules_purchase_per_settlement(
    settlement, expect_purchase
):
    order = SimpleNamespace(
        id=41, pay_way=SimpleNamespace(settlement=settlement.value)
    )

    with (
        mock.patch("meta_capi.signals.schedule_purchase") as purchase,
        mock.patch("meta_capi.signals.schedule_initiate_checkout") as ic,
    ):
        _on_order_created(sender=None, order=order)

    ic.assert_called_once_with(41)
    if expect_purchase:
        purchase.assert_called_once_with(41)
    else:
        purchase.assert_not_called()


@pytest.mark.parametrize("amount", [Decimal("9.99"), None])
def test_order_refunded_schedules_refund_with_amount(amount):
    with mock.patch("meta_capi.signals.schedule_refund") as refund:
        _on_order_refunded(
            sender=None, order=SimpleNamespace(id=43), amount=amount
        )

    refund.assert_called_once_with(43, amount)
