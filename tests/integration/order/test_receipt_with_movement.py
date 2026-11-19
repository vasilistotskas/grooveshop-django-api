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
from order.accounting.movement import (
    ReceiptMovement,
    receipt_movement,
    unanswered_sends,
)
from order.accounting.mydata.exceptions import (
    MyDataAuthError,
    MyDataTransportError,
)
from order.accounting.mydata.service import MarkLookup
from order.accounting.mydata.types import ERROR_DUPLICATE_UID, ERROR_NO_ANSWER
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
        assert movement.dispatch_date == timezone.localdate()

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

    def test_it_is_dated_the_day_it_is_issued(self):
        """The day the voucher is created: never the next business day after
        the cutoff, which would date the document after the movement
        started, and never earlier, which AADE refuses (error 280)."""
        movement = receipt_movement(Invoice(order=_acs_order(), movement={}))

        assert movement.dispatch_date == timezone.localdate()

    def test_a_document_sent_is_sent_again_as_issued(self):
        """Its dispatch date included, even once past: AADE says nothing
        of error 280 for a ``transmissionFailure`` document, which was
        issued during the outage. Only a refusal of its content rebuilds
        it; error 228 means AADE holds it as it was sent."""
        order = _acs_order()
        stored = receipt_movement(Invoice(order=order, movement={}))
        past = ReceiptMovement(
            dispatch_date=timezone.localdate() - timedelta(days=2),
            loading=stored.loading,
            delivery=stored.delivery,
            goods=stored.goods,
        )

        def sent_as(status, code=""):
            return Invoice(
                order=order,
                movement=past.as_json(),
                mydata_status=status,
                mydata_error_code=code,
            )

        assert receipt_movement(sent_as(MyDataStatus.SUBMITTED)) == past
        # AADE holds nothing under its uid: a plain resend is dated today
        # (error 280), one whose parcel left unregistered keeps its date.
        plain = sent_as(MyDataStatus.SUBMITTED)
        assert (
            receipt_movement(plain, nothing_registered=True).dispatch_date
            == timezone.localdate()
        )
        plain.transmission_failed_at = timezone.now()
        assert receipt_movement(plain, nothing_registered=True) == past
        assert (
            receipt_movement(
                sent_as(MyDataStatus.REJECTED, ERROR_DUPLICATE_UID)
            )
            == past
        )
        # AADE refused it, so nothing was registered: it is built anew.
        for code in ("XSD", "BUILD", "101"):
            assert (
                receipt_movement(
                    sent_as(MyDataStatus.REJECTED, code)
                ).dispatch_date
                == timezone.localdate()
            )

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
    def test_one_unanswered_send_is_no_loss_of_connection(
        self, django_capture_on_commit_callbacks
    ):
        """While the parcel is with the store the receipt is only a send
        that went unanswered: no indication is stamped on it."""
        invoice = _unanswered(_acs_order(), django_capture_on_commit_callbacks)

        assert invoice.invoice_number
        assert invoice.mydata_mark is None
        assert invoice.transmission_failed_at is None
        assert invoice.mydata_status == MyDataStatus.SUBMITTED
        assert invoice.mydata_error_code == ERROR_NO_ANSWER

    def test_the_sweep_sends_it_again_plainly_while_the_parcel_is_here(
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
        assert sent.transmission_failure is None
        assert sent.is_delivery_note
        invoice.refresh_from_db()
        assert invoice.mydata_mark is not None
        assert invoice.movement == issued

    def test_once_the_parcel_leaves_unregistered_every_send_says_so(
        self, django_capture_on_commit_callbacks
    ):
        """Α.1233/2026 annex §12.1: its movement started during the loss
        of connection, so from the moment the parcel leaves, every
        transmission carries ``transmissionFailure`` 3."""
        order = _acs_order()
        invoice = _unanswered(order, django_capture_on_commit_callbacks)

        with (
            mydata_enabled(),
            FakeAade() as aade,
            django_capture_on_commit_callbacks(execute=True),
        ):
            OrderService.update_order_status(order, OrderStatus.SHIPPED)

        invoice.refresh_from_db()
        assert invoice.transmission_failed_at is not None
        # Sent at once, with the indication.
        [sent] = aade.receipts
        assert sent.transmission_failure == "3"
        assert invoice.mydata_mark is not None

    def test_a_send_unanswered_after_the_parcel_left_is_stamped_too(
        self, django_capture_on_commit_callbacks
    ):
        """The parcel left while a send was in flight (its intent had
        cleared the unanswered mark), and that send then went unanswered:
        whichever of the two comes second stamps the receipt."""
        order = _acs_order()
        invoice = _unanswered(order, django_capture_on_commit_callbacks)
        Invoice.objects.filter(pk=invoice.pk).update(mydata_error_code="")

        with (
            mydata_enabled(),
            FakeAade(),
            patch(
                "order.accounting.mydata.client.MyDataClient.send_invoices",
                side_effect=MyDataTransportError("connection refused"),
            ),
            patch.object(send_invoice_to_mydata, "max_retries", 0),
            django_capture_on_commit_callbacks(execute=True),
        ):
            # SHIPPED finds no unanswered send, and sends the receipt
            # again; that send goes unanswered too.
            OrderService.update_order_status(order, OrderStatus.SHIPPED)

        invoice.refresh_from_db()
        assert invoice.mydata_error_code == ERROR_NO_ANSWER
        assert invoice.transmission_failed_at is not None

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
            # Its registration queues the cancel at once.
            _untouched_for_a_while(invoice)
            with django_capture_on_commit_callbacks(execute=True):
                retry_receipts_with_mydata()

        invoice.refresh_from_db()
        [sent] = aade.receipts
        # The parcel never left: no loss-of-connection indication.
        assert sent.transmission_failure is None
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


def _deferred_on_commit(func, using=None, robust=False):
    """Django's own ``transaction.on_commit``: ``tests/conftest.py`` runs
    commit hooks at once, and these tests need what happens before them."""
    from django.db import transaction

    transaction.get_connection(using).on_commit(func, robust)


@pytest.mark.usefixtures("dispatch_point")
class TestWhatHappensBetweenTwoSteps:
    """Second independent review of #760: what another worker can see or do
    between two steps of a cancel, a send or an issue."""

    def test_the_cancel_sets_the_receipt_aside_before_it_commits(
        self, django_capture_on_commit_callbacks
    ):
        """No credit can be created for it, and no email sent, between the
        cancel and its commit hooks: the hold is in the cancel itself."""
        from order.models import RetailCredit
        from order.tasks import send_invoice_email

        with mydata_enabled(), FakeAade() as aade:
            order, invoice = _registered_cod_receipt(
                django_capture_on_commit_callbacks
            )
            with (
                patch("django.db.transaction.on_commit", _deferred_on_commit),
                django_capture_on_commit_callbacks(execute=False),
                patch(
                    "shipping.services.ShippingService.cancel_shipment",
                    return_value=True,
                ),
            ):
                OrderService.cancel_order(
                    order, "changed my mind", customer_initiated=False
                )
                invoice.refresh_from_db()
                assert invoice.cancel_requested_at is not None
                # Another worker runs the credit and email tasks now.
                issue_pending_credits(order.id)
                send_invoice_email(order.id)

        assert not RetailCredit.objects.filter(order=order).exists()
        assert aade.credits == []

    def test_a_receipt_not_for_the_customer_is_never_emailed(
        self, django_capture_on_commit_callbacks, mailoutbox
    ):
        """The email task reads the receipt when it runs: a movement receipt
        whose parcel has not left is not sent, whoever queued the email."""
        from order.tasks import send_invoice_email

        with mydata_enabled(), FakeAade():
            invoice = _issue(_acs_order(), django_capture_on_commit_callbacks)
            send_invoice_email(invoice.order_id)

        assert not _emailed(mailoutbox, invoice)

    def test_shipped_while_the_mark_was_stored_it_is_still_emailed(
        self, django_capture_on_commit_callbacks, mailoutbox
    ):
        """SHIPPED commits after the order was loaded and before the MARK is
        stored: its handler saw no MARK, so the send reads the order again
        after storing it, and emails."""
        from order.tasks import _submit_document_to_mydata

        order = _acs_order()

        def registered_then_shipped(task, order, document):
            row = _submit_document_to_mydata(task, order, document)
            type(order).objects.filter(pk=order.pk).update(
                status=OrderStatus.SHIPPED, shipped_at=timezone.now()
            )
            return row

        with (
            mydata_enabled(),
            FakeAade(),
            patch(
                "order.tasks._submit_document_to_mydata",
                side_effect=registered_then_shipped,
            ),
        ):
            invoice = _issue(order, django_capture_on_commit_callbacks)

        assert invoice.mydata_mark is not None
        assert _emailed(mailoutbox, invoice)

    def test_one_canceled_as_issued_in_error_is_not_emailed_at_shipped(
        self, django_capture_on_commit_callbacks, mailoutbox
    ):
        order = _acs_order()
        with mydata_enabled(), FakeAade():
            invoice = _issue(order, django_capture_on_commit_callbacks)
            Invoice.objects.filter(pk=invoice.pk).update(
                mydata_status=MyDataStatus.CANCELED
            )
            with django_capture_on_commit_callbacks(execute=True):
                OrderService.update_order_status(order, OrderStatus.SHIPPED)

        assert not _emailed(mailoutbox, invoice)

    def test_an_order_canceled_since_it_was_read_gets_no_receipt(self):
        """The issuing task read the order before the cancel committed; the
        locked row decides."""
        from order.accounting.invoicing import (
            ReceiptNotIssuable,
            generate_invoice,
        )

        order = _acs_order()
        type(order).objects.filter(pk=order.pk).update(
            status=OrderStatus.CANCELED
        )

        with mydata_enabled(), pytest.raises(ReceiptNotIssuable):
            generate_invoice(order)

        assert not Invoice.objects.filter(order=order).exists()

    def test_a_sender_never_demotes_a_receipt_another_registered(
        self, django_capture_on_commit_callbacks
    ):
        """A late retry holding the row as it was before the MARK: it sends
        nothing, and the registered row stays as it is."""
        from order.accounting.mydata.service import submit_document

        with mydata_enabled(), FakeAade() as aade:
            invoice = _issue(_acs_order(), django_capture_on_commit_callbacks)
            stale = Invoice.objects.get(pk=invoice.pk)
            stale.mydata_mark = None
            stale.mydata_status = MyDataStatus.SUBMITTED

            row = submit_document(stale)

        assert row.invoice_mark == invoice.mydata_mark
        assert len(aade.receipts) == 1
        invoice.refresh_from_db()
        assert invoice.mydata_status == MyDataStatus.CONFIRMED
        assert invoice.mydata_mark == row.invoice_mark

    def test_a_228_after_another_sender_registered_it_is_no_refusal(
        self, django_capture_on_commit_callbacks
    ):
        """Two senders in flight: the other registers it while this one
        waits, and AADE answers this one with error 228."""
        from order.accounting.mydata.service import submit_document

        order = _acs_order()
        with mydata_enabled(), FakeAade():
            invoice = _unanswered(order, django_capture_on_commit_callbacks)
        duplicate = (
            b'<?xml version="1.0" encoding="UTF-8"?><ResponseDoc><response>'
            b"<index>1</index><statusCode>ValidationError</statusCode>"
            b"<errors><error><message>duplicate</message><code>228</code>"
            b"</error></errors></response></ResponseDoc>"
        )

        def the_other_sender_registers_it(xml_bytes):
            Invoice.objects.filter(pk=invoice.pk).update(
                mydata_status=MyDataStatus.CONFIRMED,
                mydata_mark=400000000000999,
            )
            return duplicate

        Invoice.objects.filter(pk=invoice.pk).update(mydata_error_code="")
        invoice.refresh_from_db()
        with (
            mydata_enabled(),
            patch(
                "order.accounting.mydata.client.MyDataClient.send_invoices",
                side_effect=the_other_sender_registers_it,
            ),
            # AADE cannot say whether it holds it, before the send or after.
            patch(
                "order.accounting.mydata.service.look_up_mark",
                return_value=MarkLookup.UNKNOWN,
            ),
        ):
            row = submit_document(invoice)

        assert row.invoice_mark == 400000000000999
        invoice.refresh_from_db()
        assert invoice.mydata_status == MyDataStatus.CONFIRMED
        assert invoice.mydata_error_code == ""

    def test_two_settles_of_one_receipt_send_one_cancel(
        self, django_capture_on_commit_callbacks
    ):
        """A second settle while the first waits for AADE (the sweep, the
        courier's answer) returns at once: one CancelInvoice, and no
        "already canceled" error left on the receipt."""
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
            answer = aade._cancel
            second = []

            def cancel_while_another_settles(mark, **kwargs):
                if not second:
                    second.append(settle_canceled_receipt(order.id))
                return answer(mark, **kwargs)

            with (
                patch(
                    "order.accounting.mydata.client.MyDataClient."
                    "cancel_invoice",
                    side_effect=cancel_while_another_settles,
                ) as cancel,
                django_capture_on_commit_callbacks(execute=True),
            ):
                assert settle_canceled_receipt(order.id) == "canceled"

        assert second == ["settling_elsewhere"]
        assert cancel.call_count == 1
        invoice.refresh_from_db()
        assert invoice.mydata_status == MyDataStatus.CANCELED
        assert invoice.mydata_error_message == ""


@pytest.mark.usefixtures("dispatch_point")
class TestARefusedReceiptOfACanceledOrder:
    def test_it_is_set_aside_and_canceled_once_staff_correct_it(
        self, django_capture_on_commit_callbacks
    ):
        """Never registered, never handed out: set aside like any other,
        staff are told to correct it, and once it is registered it is
        canceled in myDATA, with no credit."""
        refusal = (
            b'<?xml version="1.0" encoding="UTF-8"?><ResponseDoc><response>'
            b"<index>1</index><statusCode>ValidationError</statusCode>"
            b"<errors><error><message>bad</message><code>101</code>"
            b"</error></errors></response></ResponseDoc>"
        )
        order = _cod_order()
        with (
            mydata_enabled(),
            patch(
                "order.accounting.mydata.client.MyDataClient.send_invoices",
                return_value=refusal,
            ),
        ):
            invoice = _issue(order, django_capture_on_commit_callbacks)
        assert invoice.mydata_status == MyDataStatus.REJECTED

        with mydata_enabled(), FakeAade() as aade:
            _staff_cancel(
                order, django_capture_on_commit_callbacks, return_value=True
            )
            invoice.refresh_from_db()
            assert invoice.cancel_requested_at is not None
            assert any("Correct it" in note for note in _notes(order))
            # Staff correct it and send it again.
            with django_capture_on_commit_callbacks(execute=True):
                send_invoice_to_mydata(order.id)

        invoice.refresh_from_db()
        assert invoice.mydata_status == MyDataStatus.CANCELED
        assert aade.cancelled == [invoice.mydata_mark]
        assert aade.credits == []


@pytest.mark.usefixtures("dispatch_point")
class TestAMovementTheReceiptDoesNotCover:
    """The voucher made again after the receipt was registered: staff are
    told at SHIPPED, when the movement that took place is known."""

    def test_a_parcel_sent_elsewhere_is_flagged(
        self, django_capture_on_commit_callbacks, caplog
    ):
        order = _acs_order()
        with mydata_enabled(), FakeAade():
            invoice = _issue(order, django_capture_on_commit_callbacks)
            type(order).objects.filter(pk=order.pk).update(
                street="Πατησίων", street_number="20", zipcode="10677"
            )
            order.refresh_from_db()
            with django_capture_on_commit_callbacks(execute=True):
                OrderService.update_order_status(order, OrderStatus.SHIPPED)

        [note] = [n for n in _notes(order) if "movement indication" in n]
        assert invoice.invoice_number in note
        assert "Ερμού 10, 10563 Αθήνα" in note
        assert "Πατησίων 20, 10677 Αθήνα" in note
        assert any(r.levelname == "ERROR" for r in caplog.records)

    def test_a_parcel_that_left_too_late_is_flagged(
        self, django_capture_on_commit_callbacks
    ):
        order = _acs_order()
        with mydata_enabled(), FakeAade():
            invoice = _issue(order, django_capture_on_commit_callbacks)
            Invoice.objects.filter(pk=invoice.pk).update(
                issue_date=timezone.localdate() - timedelta(days=6)
            )
            with django_capture_on_commit_callbacks(execute=True):
                OrderService.update_order_status(order, OrderStatus.SHIPPED)

        assert any("6 days after" in note for note in _notes(order))

    def test_the_movement_as_declared_is_not_flagged(
        self, django_capture_on_commit_callbacks
    ):
        order = _acs_order()
        with mydata_enabled(), FakeAade():
            _issue(order, django_capture_on_commit_callbacks)
            with django_capture_on_commit_callbacks(execute=True):
                OrderService.update_order_status(order, OrderStatus.SHIPPED)

        assert not any("movement indication" in n for n in _notes(order))


def _stalled(invoice) -> None:
    """The receipt as a sender killed mid-request leaves it: its intent
    stored (SUBMITTED, uid), no answer recorded, untouched for a while."""
    Invoice.objects.filter(pk=invoice.pk).update(
        mydata_error_code="",
        mydata_error_message="",
        updated_at=timezone.now() - timedelta(hours=1),
    )


def _issued_days_ago(invoice, days: int) -> None:
    movement = dict(invoice.movement)
    movement["dispatch_date"] = (
        timezone.localdate() - timedelta(days=days)
    ).isoformat()
    Invoice.objects.filter(pk=invoice.pk).update(movement=movement)


@pytest.mark.usefixtures("dispatch_point")
class TestASendWithNoAnswerOnRecord:
    """Stand-in review of #760: every way a send can end without an answer
    is found again, and a resend on a later day carries a date AADE takes."""

    def test_an_unreadable_answer_is_no_answer(
        self, django_capture_on_commit_callbacks
    ):
        """An HTTP 200 with an empty body says nothing of the document: it is
        recorded unanswered and retried, not a crash."""
        with (
            mydata_enabled(),
            patch(
                "order.accounting.mydata.client.MyDataClient.send_invoices",
                return_value=b"",
            ),
            patch.object(send_invoice_to_mydata, "max_retries", 0),
        ):
            invoice = _issue(_acs_order(), django_capture_on_commit_callbacks)

        assert invoice.mydata_status == MyDataStatus.SUBMITTED
        assert invoice.mydata_error_code == ERROR_NO_ANSWER

    def test_a_send_whose_worker_stopped_is_sent_again(
        self, django_capture_on_commit_callbacks
    ):
        invoice = _unanswered(_acs_order(), django_capture_on_commit_callbacks)
        _stalled(invoice)

        with (
            mydata_enabled(),
            FakeAade() as aade,
            django_capture_on_commit_callbacks(execute=True),
        ):
            assert retry_receipts_with_mydata() == 1

        assert len(aade.receipts) == 1
        invoice.refresh_from_db()
        assert invoice.mydata_mark is not None

    def test_one_in_flight_now_is_left_to_its_sender(
        self, django_capture_on_commit_callbacks
    ):
        invoice = _unanswered(_acs_order(), django_capture_on_commit_callbacks)
        Invoice.objects.filter(pk=invoice.pk).update(mydata_error_code="")

        assert not unanswered_sends(Invoice.objects.filter(pk=invoice.pk))

    def test_refused_credentials_wait_for_staff(
        self, django_capture_on_commit_callbacks
    ):
        with (
            mydata_enabled(),
            patch(
                "order.accounting.mydata.client.MyDataClient.send_invoices",
                side_effect=MyDataAuthError(
                    "AADE rejected credentials (401)", code="401"
                ),
            ),
        ):
            invoice = _issue(_acs_order(), django_capture_on_commit_callbacks)
        assert invoice.mydata_error_code == "401"
        _untouched_for_a_while(invoice)

        with mydata_enabled(), FakeAade() as aade:
            assert retry_receipts_with_mydata() == 0

        assert aade.sent == []

    def test_the_parcel_leaving_after_a_stopped_send_stamps_it(
        self, django_capture_on_commit_callbacks
    ):
        order = _acs_order()
        invoice = _unanswered(order, django_capture_on_commit_callbacks)
        _stalled(invoice)

        with (
            mydata_enabled(),
            FakeAade() as aade,
            django_capture_on_commit_callbacks(execute=True),
        ):
            OrderService.update_order_status(order, OrderStatus.SHIPPED)

        [sent] = aade.receipts
        assert sent.transmission_failure == "3"

    def test_a_canceled_orders_receipt_whose_sender_stopped_is_canceled(
        self, django_capture_on_commit_callbacks
    ):
        """Never answered, never registered, set aside by the cancel: the
        sweep sends it, and its registration cancels it in myDATA."""
        order = _cod_order()
        invoice = _unanswered(order, django_capture_on_commit_callbacks)
        _stalled(invoice)
        with mydata_enabled(), FakeAade() as aade:
            _staff_cancel(
                order, django_capture_on_commit_callbacks, return_value=True
            )
            _stalled(invoice)
            with django_capture_on_commit_callbacks(execute=True):
                retry_receipts_with_mydata()

        invoice.refresh_from_db()
        assert invoice.mydata_status == MyDataStatus.CANCELED
        assert aade.cancelled == [invoice.mydata_mark]
        assert aade.credits == []

    def test_a_plain_resend_on_a_later_day_is_dated_today(
        self, django_capture_on_commit_callbacks
    ):
        """AADE refuses a dispatchDate before the current date (error 280):
        once it says it holds nothing under the uid, the resend is dated
        today, its addresses as issued."""
        invoice = _unanswered(_acs_order(), django_capture_on_commit_callbacks)
        issued = invoice.movement
        _issued_days_ago(invoice, 1)
        _untouched_for_a_while(invoice)

        with (
            mydata_enabled(),
            FakeAade() as aade,
            django_capture_on_commit_callbacks(execute=True),
        ):
            retry_receipts_with_mydata()

        [sent] = aade.receipts
        today = timezone.localdate().isoformat()
        assert f"<dispatchDate>{today}</dispatchDate>".encode() in sent.xml
        invoice.refresh_from_db()
        assert invoice.movement == issued

    def test_not_when_aade_could_not_say_whether_it_holds_it(
        self, django_capture_on_commit_callbacks
    ):
        """A registered document keeps the date AADE has: without an answer
        to the lookup, the resend goes as issued (228 finds it if it is)."""
        invoice = _unanswered(_acs_order(), django_capture_on_commit_callbacks)
        _issued_days_ago(invoice, 1)
        _untouched_for_a_while(invoice)
        yesterday = (timezone.localdate() - timedelta(days=1)).isoformat()

        with (
            mydata_enabled(),
            FakeAade() as aade,
            patch(
                "order.accounting.mydata.client.MyDataClient."
                "request_transmitted_docs",
                side_effect=MyDataTransportError("timeout"),
            ),
            django_capture_on_commit_callbacks(execute=True),
        ):
            retry_receipts_with_mydata()

        [sent] = aade.receipts
        assert f"<dispatchDate>{yesterday}</dispatchDate>".encode() in sent.xml
