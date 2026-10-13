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
from django.db import DatabaseError
from django.utils import timezone
from djmoney.money import Money

from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models import Order
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
        # Cash is refundable: the merchant pays it back and records how
        # (``OrderService.refund_order``, ``repayment_reference``).
        assert PaymentLedger.refundable(shipment.order) == Decimal("24.48")

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


class TestTheStatusAndTheLedgerRowAreOneStep:
    """Marking a COD order paid and recording the collection are written
    together or not at all. Separately, a ledger insert that failed left the
    order COMPLETED with no row, and the payout retry then skipped it for
    already being paid."""

    @staticmethod
    def _acs_shipment():
        order = OrderFactory(
            num_order_items=0,
            status=OrderStatus.DELIVERED,
            payment_status=PaymentStatus.PENDING,
        )
        return AcsShipmentFactory(
            order=order,
            voucher_no="9772892603",
            shipment_state=AcsShipmentState.DELIVERED,
            cod_amount=_eur("24.48"),
            delivery_date=timezone.now() - timedelta(days=3),
        )

    @staticmethod
    def _boxnow_shipment():
        order = OrderFactory(
            num_order_items=0,
            status=OrderStatus.DELIVERED,
            payment_status=PaymentStatus.PENDING,
        )
        return BoxNowShipmentFactory(
            order=order,
            with_parcel=True,
            payment_mode="cod",
            amount_to_be_collected=_eur("31.90"),
        )

    @staticmethod
    def _saved(order):
        return Order.objects.get(pk=order.pk)

    def test_a_failing_ledger_insert_leaves_the_acs_order_pending(self):
        shipment = self._acs_shipment()

        with (
            patch.object(
                PaymentLedger,
                "record_cod_collection",
                side_effect=DatabaseError("insert failed"),
            ),
            patch("order.signals.order_paid.send") as order_paid,
            pytest.raises(DatabaseError),
        ):
            AcsService._mark_cod_order_paid_if_pending(shipment)

        order = self._saved(shipment.order)
        assert order.payment_status == PaymentStatus.PENDING
        assert order.payment_method != "acs_cod"
        assert not order.payment_transactions.exists()
        order_paid.assert_not_called()

    def test_the_retry_after_that_failure_settles_the_order(self):
        """The next payout run starts from the order as it really is: still
        PENDING, so it is not skipped. The retry here reuses the failed
        attempt's in-memory shipment, whose order object was mutated and
        then rolled back in the database: the status is read under the lock,
        not from that copy."""
        shipment = self._acs_shipment()
        with (
            patch.object(
                PaymentLedger,
                "record_cod_collection",
                side_effect=DatabaseError("insert failed"),
            ),
            pytest.raises(DatabaseError),
        ):
            AcsService._mark_cod_order_paid_if_pending(shipment)

        with patch.object(OrderService, "maybe_advance_to_completed"):
            AcsService._mark_cod_order_paid_if_pending(shipment)

        order = self._saved(shipment.order)
        assert order.payment_status == PaymentStatus.COMPLETED
        [row] = order.payment_transactions.all()
        assert row.provider_reference == "9772892603"

    def test_a_failing_ledger_insert_leaves_the_boxnow_order_pending(self):
        shipment = self._boxnow_shipment()

        with (
            patch.object(
                PaymentLedger,
                "record_cod_collection",
                side_effect=DatabaseError("insert failed"),
            ),
            patch("order.signals.order_paid.send") as order_paid,
            pytest.raises(DatabaseError),
        ):
            BoxNowService._mark_cod_order_paid_on_delivery(shipment)

        order = self._saved(shipment.order)
        assert order.payment_status == PaymentStatus.PENDING
        assert not order.payment_transactions.exists()
        order_paid.assert_not_called()

    def test_an_order_settled_meanwhile_is_not_settled_twice(self):
        """Two callers (the payout reconcile and the staff action) holding
        the order as PENDING: the one that gets the lock second sees the
        real status, records nothing and sends nothing."""
        shipment = self._acs_shipment()
        # The other caller got there first; this copy of the order is stale.
        Order.objects.filter(pk=shipment.order.pk).update(
            payment_status=PaymentStatus.COMPLETED
        )
        assert shipment.order.payment_status == PaymentStatus.PENDING

        with (
            patch("order.signals.order_paid.send") as order_paid,
            patch.object(OrderService, "maybe_advance_to_completed") as advance,
        ):
            AcsService._mark_cod_order_paid_if_pending(shipment)

        assert not shipment.order.payment_transactions.exists()
        order_paid.assert_not_called()
        advance.assert_not_called()

    def test_a_boxnow_order_settled_meanwhile_is_not_settled_twice(self):
        shipment = self._boxnow_shipment()
        Order.objects.filter(pk=shipment.order.pk).update(
            payment_status=PaymentStatus.COMPLETED
        )
        assert shipment.order.payment_status == PaymentStatus.PENDING

        with patch("order.signals.order_paid.send") as order_paid:
            BoxNowService._mark_cod_order_paid_on_delivery(shipment)

        assert not shipment.order.payment_transactions.exists()
        order_paid.assert_not_called()

    def test_the_settlement_follows_up_only_when_it_settled(self):
        shipment = self._acs_shipment()

        with (
            patch("order.signals.order_paid.send") as order_paid,
            patch.object(OrderService, "maybe_advance_to_completed") as advance,
        ):
            AcsService._mark_cod_order_paid_if_pending(shipment)

        order_paid.assert_called_once()
        advance.assert_called_once()
