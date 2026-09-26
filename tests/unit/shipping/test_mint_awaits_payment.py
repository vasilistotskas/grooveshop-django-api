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
    request = RequestFactory().get("/")
    request.user = get_user_model()(id=1, username="staff")
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


class TestAdminButtons:
    def test_issue_acs_voucher_now_refuses_and_says_why(self):
        from shipping_acs.admin import AcsShipmentAdmin

        admin = AcsShipmentAdmin(AcsShipment, AdminSite())
        shipment = AcsShipmentFactory(order=_unpaid_card_order())

        with (
            patch(
                "shipping_acs.tasks.create_acs_voucher_for_order.delay"
            ) as task,
            patch("django.contrib.messages.error") as error,
        ):
            response = admin.issue_voucher_now(_request(), shipment.id)

        task.assert_not_called()
        assert response.status_code == 302
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
                "shipping_boxnow.tasks.create_boxnow_shipment_for_order.delay"
            ) as task,
            patch("django.contrib.messages.error") as error,
        ):
            response = admin.create_parcel_action(_request(), shipment.id)

        task.assert_not_called()
        assert response.status_code == 302
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
                "shipping_boxnow.tasks.create_boxnow_shipment_for_order.delay"
            ) as task,
            patch("django.contrib.messages.info"),
        ):
            response = admin.create_parcel_action(_request(), shipment.id)

        task.assert_called_once_with(shipment.order_id)
        assert response.status_code == 302


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

        with (
            patch(
                "tenant.credentials.tenant_admin_recipients",
                return_value=["ops@example.com"],
            ),
            patch("django.core.mail.send_mail") as mail,
        ):
            result = check_stale_acs_shipments.run()

        assert result["alerted"] == 1
        unpaid.refresh_from_db()
        cod.refresh_from_db()
        assert unpaid.stale_alert_sent is False
        assert cod.stale_alert_sent is True
        assert str(cod.order_id) in mail.call_args.kwargs["message"]
