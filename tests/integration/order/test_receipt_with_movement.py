"""A courier or locker sale's receipt (#747): issued with movement indication
when its voucher or parcel is created, and never holding the parcel.

Owner decisions (2026-11-18): only a store whose receipts the platform issues
(``MyDataConfig.is_ready``) changes at all; shipping never waits for myDATA.
A receipt AADE does not answer for is issued under its loss-of-connection
procedure (``transmissionFailure`` 3) and sent again by a sweep; a refusal is
the store's data problem and is reported, the parcel ships either way.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone
from djmoney.money import Money

from order.accounting.invoicing import receipt_issuable
from order.accounting.movement import ReceiptMovement, receipt_movement
from order.accounting.mydata.exceptions import MyDataTransportError
from order.enum.document_type import OrderDocumentTypeEnum
from order.enum.status import OrderStatus, PaymentStatus
from order.factories.item import OrderItemFactory
from order.factories.order import OrderFactory
from order.models.invoice import Invoice, MyDataStatus
from order.tasks import (
    generate_order_invoice,
    issue_pending_credits,
    issue_receipt_for_pickup,
    retry_receipts_with_mydata,
    send_invoice_to_mydata,
    settle_canceled_receipt,
)
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory
from shipping.enum import ShippingKind
from shipping.models import ShippingProvider
from shipping_acs.factories import AcsShipmentFactory, AcsStationFactory
from shipping_boxnow.factories import BoxNowLockerFactory, BoxNowShipmentFactory
from tests.utils.mydata import FakeAade, mydata_enabled
from vat.factories import VatFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_weasyprint():
    """The PDF's bytes are not under test; its template is (below)."""
    with patch(
        "order.accounting.invoicing._render_pdf_bytes",
        return_value=b"%PDF-1.4 test",
    ):
        yield


LOADING = {
    "MYDATA_LOADING_STREET": "Τατοΐου",
    "MYDATA_LOADING_NUMBER": "5",
    "MYDATA_LOADING_POSTAL_CODE": "14561",
    "MYDATA_LOADING_CITY": "Κηφισιά",
}


@pytest.fixture
def dispatch_point():
    """The store's dispatch point, as its settings hold it."""
    with patch(
        "order.accounting.movement.store_settings",
        side_effect=lambda names: {name: LOADING[name] for name in names},
    ):
        yield


def _paid_order(provider: str | None, **kwargs):
    order = OrderFactory(
        num_order_items=0,
        document_type=OrderDocumentTypeEnum.RECEIPT,
        status=OrderStatus.PROCESSING,
        payment_status=PaymentStatus.COMPLETED,
        pay_way=PayWayFactory.create_online_payment(),
        shipping_provider=ShippingProvider.objects.get(code=provider)
        if provider
        else None,
        street="Ερμού",
        street_number="10",
        zipcode="10563",
        city="Αθήνα",
        **kwargs,
    )
    OrderItemFactory(
        order=order,
        product=ProductFactory(vat=VatFactory(value=24)),
        price=Money(Decimal("20.00"), "EUR"),
        quantity=2,
    )
    type(order).objects.filter(pk=order.pk).update(
        shipping_price=Money(0, "EUR"), payment_method_fee=Money(0, "EUR")
    )
    order.refresh_from_db()
    return order


def _acs_order(**shipment):
    order = _paid_order("acs")
    AcsShipmentFactory(order=order, **shipment)
    return order


def _issue(order, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        generate_order_invoice(order.id)
    return Invoice.objects.get(order=order)


class TestWhenTheReceiptIsIssuable:
    def test_a_carrier_order_before_it_ships(self):
        assert receipt_issuable(_acs_order())

    def test_not_one_the_merchant_delivers(self):
        assert not receipt_issuable(_paid_order("flat_rate"))

    def test_not_one_still_owed_online(self):
        order = _acs_order()
        type(order).objects.filter(pk=order.pk).update(
            status=OrderStatus.PENDING, payment_status=PaymentStatus.PENDING
        )
        order.refresh_from_db()

        assert not receipt_issuable(order)


@pytest.mark.usefixtures("dispatch_point")
class TestTheReceiptCarriesTheMovement:
    def test_a_home_delivery_goes_to_the_buyers_address(
        self, django_capture_on_commit_callbacks
    ):
        order = _acs_order()

        with mydata_enabled(), FakeAade() as aade:
            invoice = _issue(order, django_capture_on_commit_callbacks)

        [sent] = aade.receipts
        assert sent.is_delivery_note
        assert sent.transmission_failure is None
        assert b"<city>\xce\x91\xce\xb8\xce\xae\xce\xbd\xce\xb1</city>" in (
            sent.xml
        )
        assert invoice.mydata_mark is not None
        # Stored with the submission, so the registered PDF prints it.
        movement = ReceiptMovement.from_json(invoice.movement)
        assert movement.delivery.postal_code == "10563"
        assert movement.loading.city == "Κηφισιά"
        assert movement.dispatch_date >= timezone.localdate()

    def test_the_registered_pdf_prints_the_movement(
        self, django_capture_on_commit_callbacks
    ):
        from django.template.loader import render_to_string
        from django.utils.translation import override

        from order.accounting.invoicing import _build_context

        order = _acs_order()
        with mydata_enabled(), FakeAade():
            invoice = _issue(order, django_capture_on_commit_callbacks)

        with override("en"):
            html = render_to_string(
                "invoices/invoice.html",
                _build_context(order, invoice, invoice.vat_breakdown, {}),
            )

        assert "Movement of goods" in html
        assert "Loading place: Τατοΐου 5, 14561 Κηφισιά" in html
        assert "Delivery place: Ερμού 10, 10563 Αθήνα" in html

    def test_a_station_order_goes_to_the_station(self):
        station = AcsStationFactory(
            address_line_1="Φερών 19", city="Αθήνα", postal_code="10434"
        )
        order = _acs_order(
            delivery_kind=ShippingKind.PICKUP_POINT,
            station_destination=station,
        )
        invoice = Invoice(order=order, movement={})

        movement = receipt_movement(invoice)

        assert movement.delivery.street == "Φερών 19"
        assert movement.delivery.postal_code == "10434"

    def test_a_locker_order_goes_to_the_locker_whose_city_is_its_second_line(
        self,
    ):
        order = _paid_order("boxnow")
        BoxNowLockerFactory(
            external_id="apm-747",
            address_line_1="ΛΕΩΦΟΡΟΣ ΚΗΦΙΣΙΑΣ 155",
            address_line_2="ΑΘΗΝΑ",
            postal_code="14661",
        )
        BoxNowShipmentFactory(order=order, locker_external_id="apm-747")

        movement = receipt_movement(Invoice(order=order, movement={}))

        assert movement.delivery.city == "ΑΘΗΝΑ"
        assert movement.delivery.street == "ΛΕΩΦΟΡΟΣ ΚΗΦΙΣΙΑΣ 155"

    def test_an_order_the_merchant_delivers_keeps_a_plain_receipt(
        self, django_capture_on_commit_callbacks
    ):
        order = _paid_order("flat_rate")
        type(order).objects.filter(pk=order.pk).update(
            status=OrderStatus.SHIPPED
        )
        order.refresh_from_db()

        with mydata_enabled(), FakeAade() as aade:
            invoice = _issue(order, django_capture_on_commit_callbacks)

        [sent] = aade.receipts
        assert not sent.is_delivery_note
        assert invoice.movement == {}

    def test_a_stored_dispatch_date_is_kept_until_it_is_past(self):
        order = _acs_order()
        stored = receipt_movement(Invoice(order=order, movement={}))
        later = ReceiptMovement(
            dispatch_date=timezone.localdate() + timedelta(days=2),
            dispatch_time=None,
            loading=stored.loading,
            delivery=stored.delivery,
        )
        past = ReceiptMovement(
            dispatch_date=timezone.localdate() - timedelta(days=1),
            dispatch_time=None,
            loading=stored.loading,
            delivery=stored.delivery,
        )

        assert (
            receipt_movement(Invoice(order=order, movement=later.as_json()))
            == later
        )
        assert (
            receipt_movement(
                Invoice(order=order, movement=past.as_json())
            ).dispatch_date
            >= timezone.localdate()
        )

    def test_shipping_it_later_issues_nothing_again(
        self, django_capture_on_commit_callbacks
    ):
        from order.services import OrderService

        order = _acs_order()
        with mydata_enabled(), FakeAade() as aade:
            _issue(order, django_capture_on_commit_callbacks)
            with django_capture_on_commit_callbacks(execute=True):
                OrderService.update_order_status(order, OrderStatus.SHIPPED)

        assert len(aade.receipts) == 1


def test_without_a_dispatch_point_the_receipt_is_refused_before_sending(
    django_capture_on_commit_callbacks,
):
    order = _acs_order()

    with (
        patch(
            "order.accounting.movement.store_settings",
            side_effect=lambda names: dict.fromkeys(names, ""),
        ),
        mydata_enabled(),
        FakeAade() as aade,
    ):
        invoice = _issue(order, django_capture_on_commit_callbacks)

    assert aade.sent == []
    assert invoice.mydata_status == MyDataStatus.REJECTED
    assert invoice.mydata_error_code == "BUILD"
    assert "MYDATA_LOADING_POSTAL_CODE" in invoice.mydata_error_message


class TestIssuedWhenTheVoucherIsCreated:
    def test_a_store_that_issues_by_itself_gets_the_receipt_queued(self):
        with (
            mydata_enabled(),
            patch("order.tasks.dispatch_on_commit") as dispatch,
        ):
            issue_receipt_for_pickup(42)

        dispatch.assert_called_once_with(generate_order_invoice, [42])

    @pytest.mark.parametrize(
        "kwargs",
        [{"ready": False}, {"auto_submit": False}],
        ids=["issues-outside-the-platform", "issues-by-hand"],
    )
    def test_any_other_store_sees_nothing(self, kwargs):
        with (
            mydata_enabled(**kwargs),
            patch("order.tasks.dispatch_on_commit") as dispatch,
        ):
            issue_receipt_for_pickup(42)

        dispatch.assert_not_called()

    def test_the_acs_task_asks_for_it_once_the_voucher_exists(
        self, acs_configured_tenant
    ):
        from shipping_acs.tasks import create_acs_voucher_for_order

        order = _acs_order()
        client = MagicMock()
        client.return_value.billing_code = "B"
        client.return_value.create_voucher.return_value = {
            "Voucher_No": "9810000747"
        }

        with (
            patch("shipping_acs.services.AcsClient", client),
            patch("order.tasks.issue_receipt_for_pickup") as issue,
        ):
            result = create_acs_voucher_for_order.run(order.id)

        assert result["status"] == "ok"
        issue.assert_called_once_with(order.id)

    @pytest.mark.parametrize("ready", [True, False])
    def test_the_voucher_never_waits_for_the_receipt(
        self, acs_configured_tenant, ready
    ):
        """Whether or not the store issues through the platform, and with
        no receipt registered, the voucher is created as before."""
        from shipping_acs.services import AcsService

        order = _acs_order()
        client = MagicMock()
        client.return_value.billing_code = "B"
        client.return_value.create_voucher.return_value = {
            "Voucher_No": "9810000748"
        }

        with (
            mydata_enabled(ready=ready),
            patch("shipping_acs.services.AcsClient", client),
        ):
            shipment = AcsService.create_voucher_for_order(order)

        assert shipment.voucher_no == "9810000748"
        assert not Invoice.objects.filter(order=order).exists()


@pytest.mark.usefixtures("dispatch_point")
class TestWhenMyDataDoesNotAnswer:
    def _unanswered(self, order, django_capture_on_commit_callbacks):
        with (
            mydata_enabled(),
            patch(
                "order.accounting.mydata.client.MyDataClient.send_invoices",
                side_effect=MyDataTransportError("connection refused"),
            ),
            patch.object(send_invoice_to_mydata, "max_retries", 0),
        ):
            return _issue(order, django_capture_on_commit_callbacks)

    def test_the_receipt_is_issued_under_the_loss_of_connection_procedure(
        self, django_capture_on_commit_callbacks
    ):
        order = _acs_order()

        invoice = self._unanswered(order, django_capture_on_commit_callbacks)

        assert invoice.invoice_number
        assert invoice.mydata_mark is None
        assert invoice.transmission_failed_at is not None
        assert invoice.mydata_status == MyDataStatus.SUBMITTED

    def test_the_sweep_sends_it_again_with_the_indication(
        self, django_capture_on_commit_callbacks
    ):
        order = _acs_order()
        invoice = self._unanswered(order, django_capture_on_commit_callbacks)
        Invoice.objects.filter(pk=invoice.pk).update(
            updated_at=timezone.now() - timedelta(hours=1)
        )

        with (
            mydata_enabled(),
            FakeAade() as aade,
            django_capture_on_commit_callbacks(execute=True),
        ):
            queued = retry_receipts_with_mydata()

        assert queued == 1
        [sent] = aade.receipts
        assert sent.transmission_failure == "3"
        assert sent.is_delivery_note
        assert Invoice.objects.get(pk=invoice.pk).mydata_mark is not None

    def test_the_sweep_leaves_a_receipt_just_tried_and_a_refused_one(
        self, django_capture_on_commit_callbacks
    ):
        just_tried = self._unanswered(
            _acs_order(), django_capture_on_commit_callbacks
        )
        refused = self._unanswered(
            _acs_order(), django_capture_on_commit_callbacks
        )
        Invoice.objects.filter(pk=refused.pk).update(
            mydata_status=MyDataStatus.REJECTED,
            updated_at=timezone.now() - timedelta(hours=1),
        )

        with mydata_enabled(), FakeAade() as aade:
            assert retry_receipts_with_mydata() == 0

        assert aade.sent == []
        assert just_tried.mydata_mark is None

    def test_a_refusal_is_no_loss_of_connection(
        self, django_capture_on_commit_callbacks
    ):
        order = _acs_order()
        refusal = (
            b'<?xml version="1.0" encoding="UTF-8"?><ResponseDoc><response>'
            b"<index>1</index><statusCode>ValidationError</statusCode>"
            b"<errors><error><message>bad</message><code>101</code></error>"
            b"</errors></response></ResponseDoc>"
        )

        with (
            mydata_enabled(),
            patch(
                "order.accounting.mydata.client.MyDataClient.send_invoices",
                return_value=refusal,
            ),
        ):
            invoice = _issue(order, django_capture_on_commit_callbacks)

        assert invoice.mydata_status == MyDataStatus.REJECTED
        assert invoice.transmission_failed_at is None


def _registered_cod_receipt(django_capture_on_commit_callbacks):
    """A cash-on-delivery ACS order whose receipt, with movement, AADE
    registered when the voucher was created: the parcel has not left."""
    from tests.utils.orders import courier_cash_order

    order = courier_cash_order(
        document_type=OrderDocumentTypeEnum.RECEIPT,
        status=OrderStatus.PROCESSING,
        payment_status=PaymentStatus.PENDING,
        shipping_provider=ShippingProvider.objects.get(code="acs"),
        street="Ερμού",
        street_number="10",
        zipcode="10563",
        city="Αθήνα",
    )
    OrderItemFactory(
        order=order,
        product=ProductFactory(vat=VatFactory(value=24)),
        price=Money(Decimal("20.00"), "EUR"),
        quantity=1,
    )
    type(order).objects.filter(pk=order.pk).update(
        shipping_price=Money(0, "EUR"), payment_method_fee=Money(0, "EUR")
    )
    order.refresh_from_db()
    AcsShipmentFactory(order=order, with_voucher=True)
    invoice = _issue(order, django_capture_on_commit_callbacks)
    assert invoice.mydata_mark is not None
    return order, invoice


def _staff_cancel(order, django_capture_on_commit_callbacks, courier):
    from order.services import OrderService

    with (
        patch("shipping.services.ShippingService.cancel_shipment", **courier),
        django_capture_on_commit_callbacks(execute=True),
    ):
        OrderService.cancel_order(
            order, "changed my mind", customer_initiated=False
        )


@pytest.mark.usefixtures("dispatch_point")
class TestTheReceiptReachesTheCustomerWithTheParcel:
    def test_not_before_the_parcel_leaves(
        self, django_capture_on_commit_callbacks, mailoutbox
    ):
        order = _acs_order()

        with mydata_enabled(), FakeAade():
            invoice = _issue(order, django_capture_on_commit_callbacks)

        assert invoice.mydata_mark is not None
        assert not invoice.is_deliverable()
        assert not [m for m in mailoutbox if m.attachments]

    def test_when_it_ships(
        self, django_capture_on_commit_callbacks, mailoutbox
    ):
        from order.services import OrderService

        order = _acs_order()
        with mydata_enabled(), FakeAade():
            invoice = _issue(order, django_capture_on_commit_callbacks)
            with django_capture_on_commit_callbacks(execute=True):
                OrderService.update_order_status(order, OrderStatus.SHIPPED)

        invoice.refresh_from_db()
        assert invoice.is_deliverable()
        assert [
            m
            for m in mailoutbox
            if any(
                a[0] == f"{invoice.invoice_number}.pdf" for a in m.attachments
            )
        ]


@pytest.mark.usefixtures("dispatch_point")
class TestCanceledBeforeThePickup:
    def test_the_receipt_is_canceled_in_mydata_and_not_credited(
        self, django_capture_on_commit_callbacks
    ):
        from order.models import RetailCredit

        with mydata_enabled(), FakeAade() as aade:
            order, invoice = _registered_cod_receipt(
                django_capture_on_commit_callbacks
            )
            _staff_cancel(
                order,
                django_capture_on_commit_callbacks,
                {"return_value": True},
            )
            # The courier answered after the cancel committed: the sweep
            # (or the pending-cancel sweep) settles it.
            assert settle_canceled_receipt(order.id) == "canceled"
            issue_pending_credits(order.id)

        invoice.refresh_from_db()
        assert invoice.mydata_status == MyDataStatus.CANCELED
        assert aade.cancelled == [invoice.mydata_mark]
        assert aade.credits == []
        assert not RetailCredit.objects.filter(order=order).exists()

    def test_a_customer_cancel_settles_it_at_once(
        self, django_capture_on_commit_callbacks
    ):
        from order.services import OrderService

        with mydata_enabled(), FakeAade() as aade:
            order, invoice = _registered_cod_receipt(
                django_capture_on_commit_callbacks
            )
            with (
                patch(
                    "shipping.services.ShippingService.cancel_shipment",
                    return_value=True,
                ),
                django_capture_on_commit_callbacks(execute=True),
            ):
                OrderService.cancel_order(order, "no longer needed")

        invoice.refresh_from_db()
        assert invoice.mydata_status == MyDataStatus.CANCELED
        assert aade.cancelled == [invoice.mydata_mark]
        assert aade.credits == []

    def test_a_parcel_the_courier_could_not_stop_is_credited_as_today(
        self, django_capture_on_commit_callbacks
    ):
        from shipping.exceptions import CarrierError

        with mydata_enabled(), FakeAade() as aade:
            order, invoice = _registered_cod_receipt(
                django_capture_on_commit_callbacks
            )
            _staff_cancel(
                order,
                django_capture_on_commit_callbacks,
                {"side_effect": CarrierError("voucher on a pickup list")},
            )
            with django_capture_on_commit_callbacks(execute=True):
                assert settle_canceled_receipt(order.id) == "credited"

        invoice.refresh_from_db()
        assert invoice.mydata_status == MyDataStatus.CONFIRMED
        assert invoice.cancel_requested_at is None
        assert aade.cancelled == []
        # The unpaid COD order closed: one settlement credit, as before.
        assert len(aade.credits) == 1

    def test_no_answer_from_mydata_is_retried_by_the_sweep(
        self, django_capture_on_commit_callbacks
    ):
        with mydata_enabled(), FakeAade() as aade:
            order, invoice = _registered_cod_receipt(
                django_capture_on_commit_callbacks
            )
            _staff_cancel(
                order,
                django_capture_on_commit_callbacks,
                {"return_value": True},
            )
            with (
                patch(
                    "order.accounting.mydata.client.MyDataClient.cancel_invoice",
                    side_effect=MyDataTransportError("timeout"),
                ),
                patch.object(settle_canceled_receipt, "max_retries", 0),
            ):
                assert settle_canceled_receipt(order.id) == "myDATA_unreachable"
            Invoice.objects.filter(pk=invoice.pk).update(
                updated_at=timezone.now() - timedelta(hours=1)
            )
            with django_capture_on_commit_callbacks(execute=True):
                assert retry_receipts_with_mydata() == 1

        invoice.refresh_from_db()
        assert invoice.mydata_status == MyDataStatus.CANCELED
        assert aade.cancelled == [invoice.mydata_mark]

    def test_a_refused_cancel_is_noted_and_credited(
        self, django_capture_on_commit_callbacks
    ):
        from order.models.history import OrderHistory

        refusal = (
            b'<?xml version="1.0" encoding="UTF-8"?><ResponseDoc><response>'
            b"<index>1</index><statusCode>ValidationError</statusCode>"
            b"<errors><error><message>cannot be cancelled</message>"
            b"<code>279</code></error></errors></response></ResponseDoc>"
        )
        with mydata_enabled(), FakeAade() as aade:
            order, _invoice = _registered_cod_receipt(
                django_capture_on_commit_callbacks
            )
            _staff_cancel(
                order,
                django_capture_on_commit_callbacks,
                {"return_value": True},
            )
            with (
                patch(
                    "order.accounting.mydata.client.MyDataClient.cancel_invoice",
                    return_value=refusal,
                ),
                django_capture_on_commit_callbacks(execute=True),
            ):
                assert settle_canceled_receipt(order.id) == "credited"

        assert len(aade.credits) == 1
        assert OrderHistory.objects.filter(
            order=order, new_value__note__icontains="279"
        ).exists()

    def test_a_store_that_does_not_issue_through_the_platform_is_untouched(
        self, django_capture_on_commit_callbacks
    ):
        with mydata_enabled(), FakeAade():
            order, invoice = _registered_cod_receipt(
                django_capture_on_commit_callbacks
            )
        with mydata_enabled(ready=False), FakeAade() as aade:
            _staff_cancel(
                order,
                django_capture_on_commit_callbacks,
                {"return_value": True},
            )

        invoice.refresh_from_db()
        assert invoice.cancel_requested_at is None
        assert invoice.mydata_status == MyDataStatus.CONFIRMED
        assert aade.cancelled == []
