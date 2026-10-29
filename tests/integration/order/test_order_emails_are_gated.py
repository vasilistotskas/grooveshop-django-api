"""The carrier arrival emails are sent only while their carrier is active.

``shipping.acs_out_for_delivery`` and ``shipping.boxnow_at_locker`` list a
``CarrierGate``. A closed gate is a deliberate no-op for the email, not a
failure of the task: the in-app notification beside it is independent and
still happens, and the task says which of the two it did.
"""

from __future__ import annotations

import pytest
from django.core import mail

from order.factories.order import OrderFactory
from shipping.models import ShippingProvider
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.tasks import acs_send_arrival_notification
from shipping_boxnow.factories import (
    BoxNowParcelEventFactory,
    BoxNowShipmentFactory,
)
from shipping_boxnow.tasks import boxnow_send_arrival_notification

pytestmark = pytest.mark.django_db


def _carrier(code: str, *, active: bool) -> None:
    ShippingProvider.objects.filter(code=code).update(is_active=active)


class TestAcsArrival:
    def test_an_active_carrier_sends_it(self):
        order = OrderFactory(num_order_items=0)
        shipment = AcsShipmentFactory(order=order, with_voucher=True)
        _carrier("acs", active=True)
        mail.outbox.clear()

        result = acs_send_arrival_notification(shipment.id)

        assert result["status"] == "sent"
        assert [message.to for message in mail.outbox] == [[order.email]]

    def test_an_inactive_carrier_sends_no_email_and_says_so(self):
        order = OrderFactory(num_order_items=0)
        shipment = AcsShipmentFactory(order=order, with_voucher=True)
        _carrier("acs", active=False)
        mail.outbox.clear()

        result = acs_send_arrival_notification(shipment.id)

        assert result["status"] == "email_not_sent"
        assert mail.outbox == []


class TestBoxNowArrival:
    def test_an_active_carrier_sends_it(self):
        order = OrderFactory(num_order_items=0)
        event = BoxNowParcelEventFactory(
            shipment=BoxNowShipmentFactory(order=order, with_parcel=True)
        )
        _carrier("boxnow", active=True)
        mail.outbox.clear()

        result = boxnow_send_arrival_notification(event.id)

        assert result["status"] == "sent"
        assert [message.to for message in mail.outbox] == [[order.email]]

    def test_an_inactive_carrier_sends_no_email_and_says_so(self):
        order = OrderFactory(num_order_items=0)
        event = BoxNowParcelEventFactory(
            shipment=BoxNowShipmentFactory(order=order, with_parcel=True)
        )
        _carrier("boxnow", active=False)
        mail.outbox.clear()

        result = boxnow_send_arrival_notification(event.id)

        assert result["status"] == "email_not_sent"
        assert mail.outbox == []
