"""A courier or locker sale's receipt (#747): issued with movement indication
when its voucher or parcel is recorded, and never holding the parcel.

Owner decisions (2026-11-18): only a store whose receipts the platform issues
(``MyDataConfig.is_ready``), and that has set its dispatch point, changes at
all; shipping never waits for myDATA. A receipt AADE does not answer for is
issued under its loss-of-connection procedure (``transmissionFailure`` 3)
and sent again by a sweep; a refusal is the store's data problem and is
reported. An order canceled before its parcel left has its unused receipt
canceled in myDATA, not credited.
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
from order.accounting.mydata.exceptions import (
    MyDataAuthError,
    MyDataTransportError,
)
from order.enum.document_type import OrderDocumentTypeEnum
from order.enum.status import OrderStatus, PaymentStatus
from order.factories.item import OrderItemFactory
from order.factories.order import OrderFactory
from order.models.history import OrderHistory
from order.models.invoice import Invoice, MyDataStatus
from order.services import OrderService
from order.tasks import (
    finish_pending_shipment_cancels,
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
from shipping.exceptions import CarrierError, CarrierUnavailableError
from shipping.models import ShippingProvider
from shipping_acs.factories import AcsShipmentFactory, AcsStationFactory
from shipping_boxnow.factories import BoxNowLockerFactory, BoxNowShipmentFactory
from tests.utils.mydata import FakeAade, mydata_enabled
from tests.utils.orders import courier_cash_order
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
        tracking_number="",
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


def _notes(order) -> list[str]:
    return [
        row.new_value["note"]
        for row in OrderHistory.objects.filter(order=order, change_type="NOTE")
    ]


class TestWhenTheReceiptIsIssuable:
    @pytest.mark.usefixtures("dispatch_point")
    def test_a_carrier_order_before_it_ships(self):
        assert receipt_issuable(_acs_order())

    def test_not_before_the_store_sets_its_dispatch_point(self):
        """It keeps today's receipt at SHIPPED instead."""
        assert not receipt_issuable(_acs_order())

    @pytest.mark.usefixtures("dispatch_point")
    def test_not_one_the_merchant_delivers(self):
        assert not receipt_issuable(_paid_order("flat_rate"))

    @pytest.mark.usefixtures("dispatch_point")
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
        assert "<city>Αθήνα</city>".encode() in sent.xml
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

        movement = receipt_movement(Invoice(order=order, movement={}))

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

    def test_a_document_sent_is_sent_again_as_issued(self):
        """Its dispatch date included, even once past: AADE says nothing
        of error 280 for a ``transmissionFailure`` document, which was
        issued during the outage."""
        order = _acs_order()
        stored = receipt_movement(Invoice(order=order, movement={}))
        past = ReceiptMovement(
            dispatch_date=timezone.localdate() - timedelta(days=2),
            dispatch_time=None,
            loading=stored.loading,
            delivery=stored.delivery,
        )

        sent = Invoice(
            order=order,
            movement=past.as_json(),
            mydata_status=MyDataStatus.SUBMITTED,
        )
        refused = Invoice(
            order=order,
            movement=past.as_json(),
            mydata_status=MyDataStatus.REJECTED,
        )

        assert receipt_movement(sent) == past
        # AADE refused it, so nothing was registered: it is built anew.
        assert receipt_movement(refused).dispatch_date >= timezone.localdate()

    def test_shipping_it_later_issues_nothing_again(
        self, django_capture_on_commit_callbacks
    ):
        order = _acs_order()
        with mydata_enabled(), FakeAade() as aade:
            _issue(order, django_capture_on_commit_callbacks)
            with django_capture_on_commit_callbacks(execute=True):
                OrderService.update_order_status(order, OrderStatus.SHIPPED)

        assert len(aade.receipts) == 1


def test_without_a_dispatch_point_the_receipt_waits_for_shipment_and_is_plain(
    django_capture_on_commit_callbacks,
):
    """A deploy never turns a store's courier receipts into refusals: until
    the dispatch point is set they are issued at SHIPPED, as before."""
    order = _acs_order()

    with mydata_enabled(), FakeAade() as aade:
        with django_capture_on_commit_callbacks(execute=True):
            generate_order_invoice(order.id)
        assert not Invoice.objects.filter(order=order).exists()
        with django_capture_on_commit_callbacks(execute=True):
            OrderService.update_order_status(order, OrderStatus.SHIPPED)

    [sent] = aade.receipts
    assert not sent.is_delivery_note
    assert Invoice.objects.get(order=order).movement == {}


class TestIssuedWhenTheShipmentIsLive:
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

    @pytest.mark.usefixtures("dispatch_point")
    def test_the_first_tracking_number_of_a_carrier_order_issues_it(
        self, django_capture_on_commit_callbacks
    ):
        """Where every carrier path records a live voucher or parcel."""
        order = _acs_order()

        with (
            mydata_enabled(),
            FakeAade() as aade,
            django_capture_on_commit_callbacks(execute=True),
        ):
            order.add_tracking_info("9810000747", "acs")

        [sent] = aade.receipts
        assert sent.is_delivery_note

    @pytest.mark.usefixtures("dispatch_point")
    def test_a_tracking_number_on_an_order_the_merchant_delivers_does_not(
        self, django_capture_on_commit_callbacks
    ):
        order = _paid_order("flat_rate")

        with (
            mydata_enabled(),
            FakeAade() as aade,
            django_capture_on_commit_callbacks(execute=True),
        ):
            order.add_tracking_info("ELTA-1", "elta")

        assert aade.sent == []

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


def _unanswered(order, django_capture_on_commit_callbacks):
    """The receipt issued while myDATA does not answer."""
    with (
        mydata_enabled(),
        FakeAade(),
        patch(
            "order.accounting.mydata.client.MyDataClient.send_invoices",
            side_effect=MyDataTransportError("connection refused"),
        ),
        patch.object(send_invoice_to_mydata, "max_retries", 0),
    ):
        return _issue(order, django_capture_on_commit_callbacks)


def _untouched_for_a_while(invoice) -> None:
    Invoice.objects.filter(pk=invoice.pk).update(
        updated_at=timezone.now() - timedelta(hours=1)
    )


@pytest.mark.usefixtures("dispatch_point")
class TestWhenMyDataDoesNotAnswer:
    def test_the_receipt_is_issued_under_the_loss_of_connection_procedure(
        self, django_capture_on_commit_callbacks
    ):
        invoice = _unanswered(_acs_order(), django_capture_on_commit_callbacks)

        assert invoice.invoice_number
        assert invoice.mydata_mark is None
        assert invoice.transmission_failed_at is not None
        assert invoice.mydata_status == MyDataStatus.SUBMITTED

    def test_the_sweep_sends_it_again_as_issued_with_the_indication(
        self, django_capture_on_commit_callbacks
    ):
        invoice = _unanswered(_acs_order(), django_capture_on_commit_callbacks)
        issued = invoice.movement
        _untouched_for_a_while(invoice)

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
        invoice.refresh_from_db()
        assert invoice.mydata_mark is not None
        assert invoice.movement == issued

    def test_a_resend_finds_the_mark_of_a_send_that_registered(
        self, django_capture_on_commit_callbacks
    ):
        """The unanswered send reached AADE after all: its MARK is looked up
        before anything is sent again."""
        invoice = _unanswered(_acs_order(), django_capture_on_commit_callbacks)
        _untouched_for_a_while(invoice)

        with (
            mydata_enabled(),
            FakeAade() as aade,
            django_capture_on_commit_callbacks(execute=True),
        ):
            aade.registered[invoice.mydata_uid] = 400000000000777
            retry_receipts_with_mydata()

        assert aade.sent == []
        invoice.refresh_from_db()
        assert invoice.mydata_mark == 400000000000777

    def test_the_sweep_leaves_a_receipt_just_tried_and_a_refused_one(
        self, django_capture_on_commit_callbacks
    ):
        just_tried = _unanswered(
            _acs_order(), django_capture_on_commit_callbacks
        )
        refused = _unanswered(_acs_order(), django_capture_on_commit_callbacks)
        Invoice.objects.filter(pk=refused.pk).update(
            mydata_status=MyDataStatus.REJECTED,
            updated_at=timezone.now() - timedelta(hours=1),
        )

        with mydata_enabled(), FakeAade() as aade:
            assert retry_receipts_with_mydata() == 0

        assert aade.sent == []
        just_tried.refresh_from_db()
        assert just_tried.mydata_mark is None

    def test_a_refusal_is_no_loss_of_connection(
        self, django_capture_on_commit_callbacks
    ):
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
            invoice = _issue(_acs_order(), django_capture_on_commit_callbacks)

        assert invoice.mydata_status == MyDataStatus.REJECTED
        assert invoice.transmission_failed_at is None


@pytest.mark.usefixtures("dispatch_point")
class TestTheReceiptReachesTheCustomerWithTheParcel:
    def test_not_before_the_parcel_leaves(
        self, django_capture_on_commit_callbacks, mailoutbox
    ):
        with mydata_enabled(), FakeAade():
            invoice = _issue(_acs_order(), django_capture_on_commit_callbacks)

        assert invoice.mydata_mark is not None
        assert not invoice.is_deliverable()
        assert not [m for m in mailoutbox if m.attachments]

    def test_when_it_ships(
        self, django_capture_on_commit_callbacks, mailoutbox
    ):
        order = _acs_order()
        with mydata_enabled(), FakeAade():
            invoice = _issue(order, django_capture_on_commit_callbacks)
            with django_capture_on_commit_callbacks(execute=True):
                OrderService.update_order_status(order, OrderStatus.SHIPPED)

        invoice.refresh_from_db()
        assert invoice.is_deliverable()
        assert _emailed(mailoutbox, invoice)


def _emailed(mailoutbox, invoice) -> bool:
    return any(
        attachment[0] == f"{invoice.invoice_number}.pdf"
        for message in mailoutbox
        for attachment in message.attachments
    )


def _cod_order():
    order = courier_cash_order(
        document_type=OrderDocumentTypeEnum.RECEIPT,
        status=OrderStatus.PROCESSING,
        payment_status=PaymentStatus.PENDING,
        shipping_provider=ShippingProvider.objects.get(code="acs"),
        street="Ερμού",
        street_number="10",
        zipcode="10563",
        city="Αθήνα",
        tracking_number="",
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
    return order


def _registered_cod_receipt(django_capture_on_commit_callbacks):
    """A cash-on-delivery ACS order whose receipt, with movement, AADE
    registered when the voucher was created: the parcel has not left."""
    order = _cod_order()
    invoice = _issue(order, django_capture_on_commit_callbacks)
    assert invoice.mydata_mark is not None
    return order, invoice


def _staff_cancel(
    order, django_capture_on_commit_callbacks, *, settle=True, **courier
):
    """A staff cancel; the courier's answer is recorded after it commits,
    and settles the receipt then (``order_shipment_cancel_answered``).
    ``settle=False`` leaves the settling to a worker the test runs itself."""
    from contextlib import nullcontext

    with (
        patch("shipping.services.ShippingService.cancel_shipment", **courier),
        nullcontext()
        if settle
        else patch("order.signals.handlers.settle_canceled_receipt"),
        django_capture_on_commit_callbacks(execute=True),
    ):
        OrderService.cancel_order(
            order, "changed my mind", customer_initiated=False
        )


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
                order, django_capture_on_commit_callbacks, return_value=True
            )
            issue_pending_credits(order.id)

        invoice.refresh_from_db()
        assert invoice.mydata_status == MyDataStatus.CANCELED
        assert aade.cancelled == [invoice.mydata_mark]
        assert aade.credits == []
        assert not RetailCredit.objects.filter(order=order).exists()

    def test_a_customer_cancel_settles_it_at_once(
        self, django_capture_on_commit_callbacks
    ):
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

    def test_the_pending_cancel_sweep_settles_it_once_the_courier_answers(
        self, django_capture_on_commit_callbacks
    ):
        with mydata_enabled(), FakeAade() as aade:
            order, invoice = _registered_cod_receipt(
                django_capture_on_commit_callbacks
            )
            _staff_cancel(
                order,
                django_capture_on_commit_callbacks,
                side_effect=CarrierUnavailableError("ACS is down"),
            )
            invoice.refresh_from_db()
            assert invoice.cancel_requested_at is not None
            assert aade.cancelled == []
            type(order).objects.filter(pk=order.pk).update(
                status_updated_at=timezone.now() - timedelta(hours=1)
            )
            with (
                patch(
                    "shipping.services.ShippingService.cancel_shipment",
                    return_value=True,
                ),
                django_capture_on_commit_callbacks(execute=True),
            ):
                finish_pending_shipment_cancels()

        invoice.refresh_from_db()
        assert invoice.mydata_status == MyDataStatus.CANCELED
        assert aade.cancelled == [invoice.mydata_mark]

    def test_a_parcel_the_courier_could_not_stop_is_credited_and_delivered(
        self, django_capture_on_commit_callbacks, mailoutbox
    ):
        with mydata_enabled(), FakeAade() as aade:
            order, invoice = _registered_cod_receipt(
                django_capture_on_commit_callbacks
            )
            _staff_cancel(
                order,
                django_capture_on_commit_callbacks,
                side_effect=CarrierError("voucher on a pickup list"),
            )

        invoice.refresh_from_db()
        assert invoice.mydata_status == MyDataStatus.CONFIRMED
        assert invoice.cancel_requested_at is None
        assert aade.cancelled == []
        # The unpaid COD order closed: one settlement credit, as before.
        assert len(aade.credits) == 1
        # The receipt stands, so it reaches the customer with its credit.
        assert invoice.is_deliverable()
        assert _emailed(mailoutbox, invoice)

    def test_a_receipt_with_a_credit_is_never_canceled(
        self, django_capture_on_commit_callbacks
    ):
        """Money went back before the pickup: the receipt was used, so the
        rest of it goes the credit path."""
        from order.models import RetailCredit
        from order.models.retail_credit import CreditReason

        with mydata_enabled(), FakeAade() as aade:
            order, invoice = _registered_cod_receipt(
                django_capture_on_commit_callbacks
            )
            RetailCredit.objects.create(
                order=order,
                invoice=invoice,
                reason=CreditReason.SETTLEMENT,
                credit_number="CRD-2026-900747",
            )
            _staff_cancel(
                order, django_capture_on_commit_callbacks, return_value=True
            )

        invoice.refresh_from_db()
        assert invoice.cancel_requested_at is None
        assert invoice.mydata_status == MyDataStatus.CONFIRMED
        assert aade.cancelled == []

    def test_no_answer_from_mydata_keeps_it_set_aside_for_the_sweep(
        self, django_capture_on_commit_callbacks
    ):
        with mydata_enabled(), FakeAade() as aade:
            order, invoice = _registered_cod_receipt(
                django_capture_on_commit_callbacks
            )
            _staff_cancel(
                order,
                django_capture_on_commit_callbacks,
                settle=False,
                return_value=True,
            )
            with (
                patch(
                    "order.accounting.mydata.client.MyDataClient."
                    "cancel_invoice",
                    side_effect=MyDataTransportError("timeout"),
                ),
                pytest.raises(MyDataTransportError),
            ):
                settle_canceled_receipt(order.id)
            invoice.refresh_from_db()
            assert invoice.cancel_requested_at is not None
            assert invoice.mydata_status == MyDataStatus.CONFIRMED
            _untouched_for_a_while(invoice)
            with django_capture_on_commit_callbacks(execute=True):
                assert retry_receipts_with_mydata() == 1

        invoice.refresh_from_db()
        assert invoice.mydata_status == MyDataStatus.CANCELED
        assert aade.cancelled == [invoice.mydata_mark]
        assert aade.credits == []

    def test_a_lost_cancel_answer_never_ends_as_cancel_and_credit(
        self, django_capture_on_commit_callbacks
    ):
        """AADE canceled it and the answer was lost; the retry is refused
        as already canceled, and AADE's own record settles it."""
        with mydata_enabled(), FakeAade() as aade:
            order, invoice = _registered_cod_receipt(
                django_capture_on_commit_callbacks
            )
            aade.lose_cancel_answers = True
            _staff_cancel(
                order,
                django_capture_on_commit_callbacks,
                settle=False,
                return_value=True,
            )
            with pytest.raises(MyDataTransportError):
                settle_canceled_receipt(order.id)
            invoice.refresh_from_db()
            assert invoice.mydata_status == MyDataStatus.CONFIRMED
            with django_capture_on_commit_callbacks(execute=True):
                assert settle_canceled_receipt(order.id) == "canceled"

        invoice.refresh_from_db()
        assert invoice.mydata_status == MyDataStatus.CANCELED
        assert (
            invoice.mydata_cancellation_mark
            == aade.cancellations[invoice.mydata_mark]
        )
        assert aade.credits == []

    def test_credentials_aade_refuses_keep_it_set_aside_and_tell_staff(
        self, django_capture_on_commit_callbacks
    ):
        with mydata_enabled(), FakeAade() as aade:
            order, invoice = _registered_cod_receipt(
                django_capture_on_commit_callbacks
            )
            with patch(
                "order.accounting.mydata.client.MyDataClient.cancel_invoice",
                side_effect=MyDataAuthError("subscription key rejected"),
            ):
                _staff_cancel(
                    order,
                    django_capture_on_commit_callbacks,
                    return_value=True,
                )

        invoice.refresh_from_db()
        assert invoice.cancel_requested_at is not None
        assert invoice.mydata_status == MyDataStatus.CONFIRMED
        assert aade.credits == []
        assert any("credentials" in note for note in _notes(order))

    def test_a_refused_cancel_is_noted_and_credited(
        self, django_capture_on_commit_callbacks
    ):
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
            with patch(
                "order.accounting.mydata.client.MyDataClient.cancel_invoice",
                return_value=refusal,
            ):
                _staff_cancel(
                    order,
                    django_capture_on_commit_callbacks,
                    return_value=True,
                )

        assert len(aade.credits) == 1
        assert any("279" in note for note in _notes(order))

    def test_issued_during_an_outage_it_is_sent_then_canceled(
        self, django_capture_on_commit_callbacks
    ):
        order = _cod_order()
        invoice = _unanswered(order, django_capture_on_commit_callbacks)
        with mydata_enabled(), FakeAade() as aade:
            _staff_cancel(
                order, django_capture_on_commit_callbacks, return_value=True
            )
            invoice.refresh_from_db()
            assert invoice.cancel_requested_at is not None
            for _ in range(2):
                _untouched_for_a_while(invoice)
                with django_capture_on_commit_callbacks(execute=True):
                    retry_receipts_with_mydata()

        invoice.refresh_from_db()
        [sent] = aade.receipts
        assert sent.transmission_failure == "3"
        assert invoice.mydata_status == MyDataStatus.CANCELED
        assert aade.cancelled == [invoice.mydata_mark]
        assert aade.credits == []

    def test_a_store_that_does_not_issue_through_the_platform_is_untouched(
        self, django_capture_on_commit_callbacks
    ):
        with mydata_enabled(), FakeAade():
            order, invoice = _registered_cod_receipt(
                django_capture_on_commit_callbacks
            )
        with mydata_enabled(ready=False), FakeAade() as aade:
            _staff_cancel(
                order, django_capture_on_commit_callbacks, return_value=True
            )

        invoice.refresh_from_db()
        assert invoice.cancel_requested_at is None
        assert invoice.mydata_status == MyDataStatus.CONFIRMED
        assert aade.cancelled == []
