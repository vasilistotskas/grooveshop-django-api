"""Tests for the meta_capi signal receivers' dispatch policy.

The load-bearing rule: Purchase is scheduled at ORDER CREATION for
offline pay-ways (COD — the confirmation page is the conversion and no
payment webhook ever fires at purchase time), while online pay-ways
wait for ``order_paid`` from the provider webhook. Both hooks may fire
for the same order (e.g. the ACS COD reconcile emits ``order_paid``
days later); the event-log's unique event_id + SENT short-circuit in
the task make the second dispatch a no-op, so the policy here only
has to guarantee the FIRST dispatch happens at the right moment.
"""

from __future__ import annotations

from unittest import mock

import pytest

from meta_capi.signals import _on_order_created, _on_order_paid


class _FakePayWay:
    def __init__(self, is_online_payment: bool):
        self.is_online_payment = is_online_payment


class _FakeOrder:
    def __init__(self, order_id: int, pay_way):
        self.id = order_id
        self.pay_way = pay_way


@pytest.fixture
def schedule_mocks():
    with (
        mock.patch("meta_capi.signals.schedule_purchase") as purchase,
        mock.patch("meta_capi.signals.schedule_initiate_checkout") as ic,
    ):
        yield purchase, ic


class TestOrderCreatedDispatchPolicy:
    def test_offline_payway_schedules_purchase_and_initiate_checkout(
        self, schedule_mocks
    ):
        purchase, ic = schedule_mocks
        order = _FakeOrder(1, _FakePayWay(is_online_payment=False))

        _on_order_created(sender=None, order=order)

        ic.assert_called_once_with(1)
        purchase.assert_called_once_with(1)

    def test_online_payway_does_not_schedule_purchase(self, schedule_mocks):
        purchase, ic = schedule_mocks
        order = _FakeOrder(2, _FakePayWay(is_online_payment=True))

        _on_order_created(sender=None, order=order)

        ic.assert_called_once_with(2)
        purchase.assert_not_called()

    def test_missing_payway_does_not_schedule_purchase(self, schedule_mocks):
        purchase, ic = schedule_mocks
        order = _FakeOrder(3, None)

        _on_order_created(sender=None, order=order)

        ic.assert_called_once_with(3)
        purchase.assert_not_called()


class TestOrderPaidDispatch:
    def test_order_paid_schedules_purchase(self, schedule_mocks):
        purchase, _ic = schedule_mocks
        order = _FakeOrder(4, _FakePayWay(is_online_payment=True))

        _on_order_paid(sender=None, order=order)

        purchase.assert_called_once_with(4)
