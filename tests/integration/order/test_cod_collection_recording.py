"""Cash a courier collected is recorded on the payment ledger.

When the COD reconcile (ACS) or the delivered parcel event (BoxNow) marks
an order paid, the collection is written as a ``COD_COLLECTION`` row under
the courier's own reference (the voucher / the parcel), so the ledger
holds every way an order's money arrived. It is not a card charge: nothing
can be refunded through a provider for it.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone
from djmoney.money import Money

from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.payment_ledger import PaymentLedger
from order.services import OrderService
from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.services import AcsService
from shipping_boxnow.factories import BoxNowShipmentFactory
from shipping_boxnow.services import BoxNowService

pytestmark = pytest.mark.django_db

COLLECTION = PaymentTransactionKind.COD_COLLECTION


def _eur(amount: str) -> Money:
    return Money(Decimal(amount), "EUR")


class TestAcsCodReconcile:
    def _shipment(self, *, amount="24.48", voucher="9772892603"):
        order = OrderFactory(
            num_order_items=0,
            status=OrderStatus.DELIVERED,
            payment_status=PaymentStatus.PENDING,
        )
        return AcsShipmentFactory(
            order=order,
            voucher_no=voucher,
            shipment_state=AcsShipmentState.DELIVERED,
            cod_amount=_eur(amount),
            delivery_date=timezone.now() - timedelta(days=3),
        )

    def test_the_collection_is_recorded_under_the_voucher(self):
        shipment = self._shipment()

        with patch.object(OrderService, "maybe_advance_to_completed"):
            AcsService._mark_cod_order_paid_if_pending(shipment)

        [row] = shipment.order.payment_transactions.all()
        assert row.kind == COLLECTION
        assert row.provider == "acs_cod"
        assert row.provider_reference == "9772892603"
        assert row.amount == _eur("24.48")
        assert shipment.order.payment_method == "acs_cod"
        assert PaymentLedger.refundable(shipment.order) == 0

    def test_a_second_reconcile_of_the_same_parcel_records_nothing_more(self):
        shipment = self._shipment()

        with patch.object(OrderService, "maybe_advance_to_completed"):
            AcsService._mark_cod_order_paid_if_pending(shipment)
            # The order is paid now, so the reconcile stops there; and had
            # it not, the ledger key would absorb the repeat.
            AcsService._mark_cod_order_paid_if_pending(shipment)

        assert shipment.order.payment_transactions.count() == 1


class TestBoxNowDelivery:
    def test_the_collection_is_recorded_under_the_parcel(self):
        order = OrderFactory(
            num_order_items=0,
            status=OrderStatus.DELIVERED,
            payment_status=PaymentStatus.PENDING,
        )
        shipment = BoxNowShipmentFactory(
            order=order,
            with_parcel=True,
            payment_mode="cod",
            amount_to_be_collected=_eur("31.90"),
        )

        BoxNowService._mark_cod_order_paid_on_delivery(shipment)

        [row] = order.payment_transactions.all()
        assert row.kind == COLLECTION
        assert row.provider == "boxnow_cod"
        assert row.provider_reference == shipment.parcel_id
        assert row.amount == _eur("31.90")
        assert order.payment_method == "boxnow_cod"
