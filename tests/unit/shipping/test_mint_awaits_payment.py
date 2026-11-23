"""No courier shipment for an order the shopper still owes online.

Background (prod order 304, 2026-09-24): every order gets its carrier
shipment row at checkout, in ``pending_creation``, and the voucher is
normally minted only once the payment webhook confirms the charge. But
nothing at the mint itself checked payment, and the admin's "Issue ACS
voucher now" could mint for an unpaid card order: a voucher carrying no
cash-on-delivery amount, so the courier hands over goods nobody paid
for, and the mint moves the order to PROCESSING.

Both carriers' choke points now refuse while
``Order.awaits_online_payment`` holds; the tasks report it as a status
(no retry, no "creation failed" alert); the admin buttons say so; and the
stale-shipment digest no longer offers these rows as stranded mints.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import RequestFactory
from django.utils import timezone

from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory
from pay_way.factories import PayWayFactory
from shipping.exceptions import ShipmentAwaitingPaymentError
from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.models import AcsShipment
from shipping_boxnow.enum.parcel_state import BoxNowParcelState
from shipping_boxnow.factories import BoxNowShipmentFactory
from shipping_boxnow.models import BoxNowShipment
from tests.utils.dispatch import assert_published_once

pytestmark = pytest.mark.django_db


def _unpaid_card_order():
    return OrderFactory(
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
        pay_way=PayWayFactory.create_online_payment(),
    )


def _paid_card_order():
    return OrderFactory(
        status=OrderStatus.PROCESSING,
        payment_status=PaymentStatus.COMPLETED,
        pay_way=PayWayFactory.create_online_payment(),
    )


def _request():
    request = RequestFactory().post("/", {"_form_submitted": "on"})
    request.user = get_user_model()(id=1, username="staff", is_superuser=True)
    return request


class TestAcsMint:
    def test_refuses_an_unpaid_card_order_without_calling_acs(self):
        order = _unpaid_card_order()
        shipment = AcsShipmentFactory(order=order)
        client = MagicMock()

        with (
            patch("shipping_acs.services.AcsClient", client),
            pytest.raises(ShipmentAwaitingPaymentError),
        ):
            from shipping_acs.services import AcsService

            AcsService.create_voucher_for_order(order)

        client.return_value.create_voucher.assert_not_called()
        shipment.refresh_from_db()
        assert shipment.voucher_no is None
        assert shipment.shipment_state == AcsShipmentState.PENDING_CREATION
        # No claim left behind to block the mint the webhook will send.
        assert "mint_started_at" not in (shipment.metadata or {})
        order.refresh_from_db()
        assert order.status == OrderStatus.PENDING

    def test_mints_once_the_card_order_is_paid(self):
        from shipping_acs.services import AcsService

        order = _paid_card_order()
        AcsShipmentFactory(order=order)
        client = MagicMock()
        client.return_value.billing_code = "B"
        client.return_value.create_voucher.return_value = {
            "Voucher_No": "9810000001"
        }

        with patch("shipping_acs.services.AcsClient", client):
            shipment = AcsService.create_voucher_for_order(order)

        assert shipment.voucher_no == "9810000001"

    def test_task_reports_awaiting_payment_without_an_alert(
        self, acs_configured_tenant
    ):
        from shipping_acs.tasks import create_acs_voucher_for_order

        order = _unpaid_card_order()
        AcsShipmentFactory(order=order)

        with (
            patch("shipping_acs.services.AcsClient"),
            patch(
                "shipping.alerts.alert_admins_shipment_creation_failed"
            ) as alert,
        ):
            result = create_acs_voucher_for_order.run(order.id)

        assert result == {"status": "awaiting_payment", "order_id": order.id}
        alert.assert_not_called()


class TestBoxNowMint:
    def test_refuses_an_unpaid_card_order_without_calling_boxnow(self):
        from shipping_boxnow.services import BoxNowService

        order = _unpaid_card_order()
        shipment = BoxNowShipmentFactory(
            order=order,
            locker_external_id="4",
            parcel_state=BoxNowParcelState.PENDING_CREATION,
        )
        client = MagicMock()

        with (
            patch("shipping_boxnow.services.BoxNowClient", client),
            pytest.raises(ShipmentAwaitingPaymentError),
        ):
            BoxNowService.create_shipment_for_order(order)

        client.return_value.create_delivery_request.assert_not_called()
        shipment.refresh_from_db()
        assert shipment.parcel_id is None
        assert shipment.parcel_state == BoxNowParcelState.PENDING_CREATION

    def test_task_reports_awaiting_payment_without_an_alert(
        self, boxnow_configured_tenant
    ):
        from shipping_boxnow.tasks import create_boxnow_shipment_for_order

        order = _unpaid_card_order()
        BoxNowShipmentFactory(
            order=order,
            locker_external_id="4",
            parcel_state=BoxNowParcelState.PENDING_CREATION,
        )

        with (
            patch("shipping_boxnow.services.BoxNowClient"),
            patch(
                "shipping.alerts.alert_admins_shipment_creation_failed"
            ) as alert,
        ):
            result = create_boxnow_shipment_for_order.run(order.id)

        assert result == {"status": "awaiting_payment", "order_id": order.id}
        alert.assert_not_called()


@pytest.mark.assert_english
class TestAdminButtons:
    def test_issue_acs_voucher_now_refuses_and_says_why(self):
        from shipping_acs.admin import AcsShipmentAdmin

        admin = AcsShipmentAdmin(AcsShipment, AdminSite())
        shipment = AcsShipmentFactory(order=_unpaid_card_order())

        with (
            patch(
                "shipping_acs.tasks.create_acs_voucher_for_order.apply_async"
            ) as task,
            patch("django.contrib.messages.error") as error,
        ):
            response = admin.issue_voucher_now(_request(), shipment.id)

        task.assert_not_called()
        assert response.status_code == 200
        message = str(error.call_args.args[1])
        assert f"#{shipment.order_id}" in message
        assert "not been paid" in message

    def test_create_boxnow_parcel_now_refuses_and_says_why(self):
        from shipping_boxnow.admin import BoxNowShipmentAdmin

        admin = BoxNowShipmentAdmin(BoxNowShipment, AdminSite())
        shipment = BoxNowShipmentFactory(
            order=_unpaid_card_order(),
            parcel_state=BoxNowParcelState.PENDING_CREATION,
        )

        with (
            patch(
                "shipping_boxnow.tasks.create_boxnow_shipment_for_order.apply_async"
            ) as task,
            patch("django.contrib.messages.error") as error,
        ):
            response = admin.create_parcel_action(_request(), shipment.id)

        task.assert_not_called()
        assert response.status_code == 200
        assert "not been paid" in str(error.call_args.args[1])

    def test_create_boxnow_parcel_now_dispatches_for_a_payable_order(self):
        from shipping_boxnow.admin import BoxNowShipmentAdmin

        admin = BoxNowShipmentAdmin(BoxNowShipment, AdminSite())
        shipment = BoxNowShipmentFactory(
            order=_paid_card_order(),
            parcel_state=BoxNowParcelState.PENDING_CREATION,
        )

        with (
            patch(
                "shipping_boxnow.tasks.create_boxnow_shipment_for_order.apply_async"
            ) as task,
            patch("django.contrib.messages.info"),
        ):
            response = admin.create_parcel_action(_request(), shipment.id)

        assert_published_once(task, [shipment.order_id])
        assert response.status_code == 200


@pytest.mark.usefixtures("open_email_gates")
class TestStaleDigest:
    def test_an_unpaid_card_order_is_not_a_stranded_mint(
        self, acs_configured_tenant
    ):
        from shipping_acs.tasks import check_stale_acs_shipments

        unpaid = AcsShipmentFactory(order=_unpaid_card_order())
        cod = AcsShipmentFactory(
            order=OrderFactory(
                status=OrderStatus.PENDING,
                payment_status=PaymentStatus.PENDING,
                pay_way=PayWayFactory(),
            )
        )
        AcsShipment.objects.filter(pk__in=[unpaid.pk, cod.pk]).update(
            created_at=timezone.now() - timedelta(hours=30)
        )

        with patch(
            "emails.recipients.store_operator_addresses",
            return_value=["ops@example.com"],
        ):
            mail.outbox.clear()
            result = check_stale_acs_shipments.run()

        assert result["alerted"] == 1
        unpaid.refresh_from_db()
        cod.refresh_from_db()
        assert unpaid.stale_alert_sent is False
        assert cod.stale_alert_sent is True
        [message] = mail.outbox
        assert str(cod.order_id) in message.body

    def test_a_refunded_card_order_is_not_waiting_for_its_payment(
        self, acs_configured_tenant
    ):
        """Only an UNSETTLED online order is excused. A refunded one is
        not waiting for money, so its stranded mint is still reported."""
        from shipping_acs.tasks import check_stale_acs_shipments

        refunded = AcsShipmentFactory(
            order=OrderFactory(
                status=OrderStatus.PROCESSING,
                payment_status=PaymentStatus.PARTIALLY_REFUNDED,
                pay_way=PayWayFactory.create_online_payment(),
            )
        )
        AcsShipment.objects.filter(pk=refunded.pk).update(
            created_at=timezone.now() - timedelta(hours=30)
        )

        with patch(
            "emails.recipients.store_operator_addresses",
            return_value=["ops@example.com"],
        ):
            mail.outbox.clear()
            result = check_stale_acs_shipments.run()

        assert result["ids"] == [refunded.pk]


def _partially_refunded_card_order():
    """Paid by card, part of it refunded before its voucher exists (#649):
    nothing is owed online, and the rest still ships."""
    return OrderFactory(
        status=OrderStatus.PROCESSING,
        payment_status=PaymentStatus.PARTIALLY_REFUNDED,
        pay_way=PayWayFactory.create_online_payment(),
    )


class TestSettledPaymentGetsItsVoucher:
    """``Order.awaits_online_payment`` was ``not is_paid``, true for every
    settled status but COMPLETED: a partially refunded card order was
    refused its voucher as unpaid, and the admin's remedy said the same."""

    @pytest.mark.parametrize("status", list(PaymentStatus))
    def test_the_property_is_the_queryset_rule_for_every_status(self, status):
        from order.managers.order import awaiting_online_payment_q
        from order.models.order import Order

        order = OrderFactory(
            payment_status=status,
            pay_way=PayWayFactory.create_online_payment(),
        )

        assert order.awaits_online_payment is (
            Order.objects.filter(
                awaiting_online_payment_q(), pk=order.pk
            ).exists()
        )

    def test_acs_mints_for_a_partially_refunded_card_order(self):
        from shipping_acs.services import AcsService

        order = _partially_refunded_card_order()
        AcsShipmentFactory(order=order)
        client = MagicMock()
        client.return_value.billing_code = "B"
        client.return_value.create_voucher.return_value = {
            "Voucher_No": "9810000002"
        }

        with patch("shipping_acs.services.AcsClient", client):
            shipment = AcsService.create_voucher_for_order(order)

        assert shipment.voucher_no == "9810000002"

    def test_boxnow_mints_for_a_partially_refunded_card_order(
        self, boxnow_configured_tenant
    ):
        from shipping_boxnow.services import BoxNowService

        order = _partially_refunded_card_order()
        BoxNowShipmentFactory(
            order=order,
            locker_external_id="4",
            parcel_state=BoxNowParcelState.PENDING_CREATION,
        )
        client = MagicMock()
        client.return_value.create_delivery_request.return_value = {
            "id": "42224",
            "parcels": [{"id": "9219709201"}],
        }

        with patch("shipping_boxnow.services.BoxNowClient", client):
            shipment = BoxNowService.create_shipment_for_order(order)

        assert shipment.parcel_id == "9219709201"


def _card_order(status, payment_status):
    return OrderFactory(
        status=status,
        payment_status=payment_status,
        pay_way=PayWayFactory.create_online_payment(),
    )


class TestAnOrderThatShipsNothingGetsNoShipment:
    """Canceled, refunded as an order, or paid back in full (a full refund
    leaves the order PROCESSING): no courier shipment. ``awaits_online_
    payment`` used to refuse these by accident, as "unpaid"; with it now
    reading the settled statuses, ``Order.ships_nothing`` refuses them by
    name (#649)."""

    @pytest.mark.parametrize("payment_status", list(PaymentStatus))
    @pytest.mark.parametrize(
        "status",
        [OrderStatus.PENDING, OrderStatus.PROCESSING, OrderStatus.CANCELED],
    )
    def test_the_property_is_the_queryset_rule(self, status, payment_status):
        from order.managers.order import ships_nothing_q
        from order.models.order import Order

        order = _card_order(status, payment_status)

        assert order.ships_nothing is (
            Order.objects.filter(ships_nothing_q(), pk=order.pk).exists()
        )

    @pytest.mark.parametrize(
        ("status", "payment_status"),
        [
            # Canceled unpaid: Order.save settles the payment to CANCELED.
            (OrderStatus.CANCELED, PaymentStatus.CANCELED),
            # Canceled after payment: the cancel's refund.
            (OrderStatus.CANCELED, PaymentStatus.REFUNDED),
            # Refunded in full, still PROCESSING.
            (OrderStatus.PROCESSING, PaymentStatus.REFUNDED),
        ],
    )
    def test_acs_refuses_without_calling_acs(self, status, payment_status):
        from shipping.exceptions import ShipmentNotNeededError
        from shipping_acs.services import AcsService

        order = _card_order(status, payment_status)
        AcsShipmentFactory(order=order)
        client = MagicMock()

        with (
            patch("shipping_acs.services.AcsClient", client),
            pytest.raises(ShipmentNotNeededError),
        ):
            AcsService.create_voucher_for_order(order)

        client.return_value.create_voucher.assert_not_called()

    def test_acs_refuses_before_it_needs_the_stores_credentials(self):
        """No ACS credentials are bound here: an order that ships nothing
        is refused as such, not as a store missing its configuration."""
        from shipping.exceptions import ShipmentNotNeededError
        from shipping_acs.services import AcsService

        order = _card_order(OrderStatus.CANCELED, PaymentStatus.REFUNDED)
        AcsShipmentFactory(order=order)

        with pytest.raises(ShipmentNotNeededError):
            AcsService.create_voucher_for_order(order)

    def test_no_movement_receipt_for_an_order_paid_back_in_full(self):
        """A voucher held for an order refunded in full is not to leave, so
        it issues no receipt with movement indication (CodeRabbit on
        #798)."""
        from order.accounting.invoicing import receipt_issuable
        from order.enum.document_type import OrderDocumentTypeEnum

        order = _card_order(OrderStatus.PROCESSING, PaymentStatus.REFUNDED)
        order.document_type = OrderDocumentTypeEnum.RECEIPT.value

        with (
            patch(
                "order.accounting.invoicing.movement_receipts_on",
                return_value=True,
            ),
            patch(
                "shipping.services.ShippingService.carrier_holds_the_parcel",
                return_value=True,
            ),
        ):
            assert receipt_issuable(order) is False
            order.payment_status = PaymentStatus.COMPLETED
            assert receipt_issuable(order) is True

    def test_a_retry_after_a_cancel_in_its_backoff_mints_nothing(
        self, acs_configured_tenant
    ):
        """The cancel retired the shipment and refunded the payment while
        the task waited to retry."""
        from shipping_acs.tasks import create_acs_voucher_for_order

        order = _card_order(OrderStatus.CANCELED, PaymentStatus.REFUNDED)
        AcsShipmentFactory(
            order=order, shipment_state=AcsShipmentState.CANCELED
        )
        client = MagicMock()

        with (
            patch("shipping_acs.services.AcsClient", client),
            patch(
                "shipping.alerts.alert_admins_shipment_creation_failed"
            ) as alert,
        ):
            result = create_acs_voucher_for_order.run(order.id)

        assert result == {"status": "not_needed", "order_id": order.id}
        client.return_value.create_voucher.assert_not_called()
        alert.assert_not_called()

    def test_boxnow_refuses_an_order_paid_back_in_full(
        self, boxnow_configured_tenant
    ):
        from shipping.exceptions import ShipmentNotNeededError
        from shipping_boxnow.services import BoxNowService

        order = _card_order(OrderStatus.PROCESSING, PaymentStatus.REFUNDED)
        BoxNowShipmentFactory(
            order=order,
            locker_external_id="4",
            parcel_state=BoxNowParcelState.PENDING_CREATION,
        )
        client = MagicMock()

        with (
            patch("shipping_boxnow.services.BoxNowClient", client),
            pytest.raises(ShipmentNotNeededError),
        ):
            BoxNowService.create_shipment_for_order(order)

        client.return_value.create_delivery_request.assert_not_called()


@pytest.mark.assert_english
class TestAdminButtonsForAnOrderThatShipsNothing:
    def test_issue_acs_voucher_now_refuses_a_canceled_order(self):
        from shipping_acs.admin import AcsShipmentAdmin

        admin = AcsShipmentAdmin(AcsShipment, AdminSite())
        shipment = AcsShipmentFactory(
            order=_card_order(OrderStatus.CANCELED, PaymentStatus.CANCELED),
            shipment_state=AcsShipmentState.CANCELED,
        )

        with (
            patch(
                "shipping_acs.tasks.create_acs_voucher_for_order.apply_async"
            ) as task,
            patch("django.contrib.messages.error") as error,
        ):
            admin.issue_voucher_now(_request(), shipment.id)

        task.assert_not_called()
        assert "canceled" in str(error.call_args.args[1])
        shipment.refresh_from_db()
        assert shipment.shipment_state == AcsShipmentState.CANCELED

    def test_create_boxnow_parcel_now_refuses_a_first_parcel_of_a_canceled_order(
        self,
    ):
        from shipping_boxnow.admin import BoxNowShipmentAdmin

        admin = BoxNowShipmentAdmin(BoxNowShipment, AdminSite())
        shipment = BoxNowShipmentFactory(
            order=_card_order(OrderStatus.CANCELED, PaymentStatus.CANCELED),
            parcel_state=BoxNowParcelState.PENDING_CREATION,
        )

        with (
            patch(
                "shipping_boxnow.tasks.create_boxnow_shipment_for_order.apply_async"
            ) as task,
            patch("django.contrib.messages.error") as error,
        ):
            admin.create_parcel_action(_request(), shipment.id)

        task.assert_not_called()
        assert "canceled" in str(error.call_args.args[1])
