"""The myDATA retail document lifecycle: a receipt (11.1) when an order
ships, a credit document (11.4) for every refund of a registered receipt.

AADE's ``SendInvoices`` is the only seam that is faked
(``tests.utils.mydata.FakeAade``); everything between a status change or a
refund and the payload that would have gone to AADE is the real code path:
signals, tasks (eager), ledger, builder, persistence.

Legal anchors: ΕΛΠ (law 4308/2014) art. 12(4) (a credit document for every
refund) and art. 13(a) (the receipt is issued at delivery or the start of
the shipment). Invoices (1.1) are not issued here at all: AADE decision
Α.1128/2025 art. 1(2).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.core import mail
from django.template.loader import render_to_string
from django.test import RequestFactory
from djmoney.money import Money
from djstripe.models import Event

from order.admin import OrderAdmin
from order.credit_documents import (
    CreditExceedsReceiptError,
    create_pending_credits,
    credit_breakdown,
)
from order.enum.document_type import OrderDocumentTypeEnum
from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory, PaymentTransactionFactory
from order.factories.item import OrderItemFactory
from order.invoicing import generate_invoice, render_credit_pdf
from order.models import Invoice, Order, RetailCredit
from order.models.invoice import MyDataStatus
from order.models.retail_credit import CreditReason
from order.services import OrderService
from order.signals.handlers import handle_stripe_charge_refunded
from order.tasks import generate_order_invoice, send_invoice_to_mydata
from order.views.viva_webhook import _handle_reversal_created
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory
from tests.utils.mydata import FakeAade, mydata_enabled
from tests.utils.orders import courier_cash_order
from tests.utils.stripe_refunds import FakeStripeRefunds
from tests.utils.viva import (
    REVERSAL_CREATED_EVENT,
    FakeVivaApi,
    retrieve_refund,
    retrieve_transaction,
)
from user.factories import UserAccountFactory
from vat.factories import VatFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

PI = "pi_mydata"


@pytest.fixture(autouse=True)
def _render_and_site(settings):
    settings.SITE_NAME = "GrooveShop"
    settings.INFO_EMAIL = "support@example.com"
    settings.NUXT_BASE_URL = "http://example.com"
    settings.STATIC_BASE_URL = "http://example.com"
    settings.DEFAULT_FROM_EMAIL = "no-reply@example.com"
    with patch(
        "order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 test"
    ):
        yield


def _eur(amount: str) -> Money:
    return Money(Decimal(amount), "EUR")


def _order(
    *,
    lines=(("24", "100.00"),),
    shipping="0.00",
    fee="0.00",
    paid_online=True,
    **kwargs,
):
    """A receipt order with one line per ``(vat rate, gross)`` and, when
    paid online, Stripe's capture of the whole total on the ledger."""
    total = (
        sum(Decimal(gross) for _rate, gross in lines)
        + Decimal(shipping)
        + Decimal(fee)
    )
    kwargs.setdefault("document_type", OrderDocumentTypeEnum.RECEIPT)
    kwargs.setdefault("status", OrderStatus.PROCESSING)
    if paid_online:
        kwargs.setdefault("pay_way", PayWayFactory.create_online_payment())
        kwargs.setdefault("payment_status", PaymentStatus.COMPLETED)
        kwargs.setdefault("payment_id", PI)
        kwargs.setdefault("payment_method", "stripe")
    else:
        kwargs.setdefault("payment_status", PaymentStatus.PENDING)
        kwargs.setdefault("payment_id", "")
        kwargs.setdefault("payment_method", "")
    order = (
        OrderFactory(num_order_items=0, **kwargs)
        if paid_online
        else courier_cash_order(**kwargs)
    )
    Order.objects.filter(pk=order.pk).update(
        shipping_price=_eur(shipping),
        payment_method_fee=_eur(fee),
        amount_due=_eur(str(total)),
    )
    for rate, gross in lines:
        OrderItemFactory(
            order=order,
            product=ProductFactory(vat=VatFactory(value=Decimal(rate))),
            price=_eur(gross),
            quantity=1,
        )
    if paid_online:
        PaymentTransactionFactory(
            order=order, amount=_eur(str(total)), provider_reference=PI
        )
    order.refresh_from_db()
    return order


def _mark_shipped(order) -> None:
    """Move the row to SHIPPED without a save, so no handler runs for it."""
    Order.objects.filter(pk=order.pk).update(status=OrderStatus.SHIPPED)
    order.refresh_from_db()
    # The instance learns the move the way a save would have told it.
    order._original_status = order.status


def _receipt(order) -> Invoice:
    """The order's registered receipt: issued the way the platform issues
    it, so it carries a MARK from the (fake) AADE."""
    _mark_shipped(order)
    generate_invoice(order)
    send_invoice_to_mydata(order.id)
    invoice = Invoice.objects.get(order=order)
    assert invoice.mydata_status == MyDataStatus.CONFIRMED
    return invoice


def _credits(order):
    return RetailCredit.objects.filter(order=order).order_by("id")


class TestReceiptIssuedWhenTheOrderShips:
    def test_shipping_issues_the_receipt_exactly_once(self):
        order = _order(lines=(("24", "124.00"),))

        with mydata_enabled(), FakeAade() as aade:
            OrderService.update_order_status(order, OrderStatus.SHIPPED)
            # Later moves, and a repeat, never issue a second receipt.
            OrderService.update_order_status(order, OrderStatus.DELIVERED)
            OrderService.update_order_status(order, OrderStatus.COMPLETED)
            generate_order_invoice(order.id)

        [receipt] = aade.receipts
        assert receipt.gross == Decimal("124.00")
        assert receipt.correlated is None
        invoice = Invoice.objects.get(order=order)
        assert invoice.mydata_status == MyDataStatus.CONFIRMED
        assert invoice.mydata_mark == aade.marks[0]
        assert aade.credits == []

    def test_the_customer_gets_the_receipt_not_an_invoice(self):
        order = _order(language_code="en")

        with mydata_enabled(), FakeAade():
            OrderService.update_order_status(order, OrderStatus.SHIPPED)

        [message] = [m for m in mail.outbox if "Receipt" in m.subject]
        assert message.subject == (
            f"Receipt {Invoice.objects.get(order=order).invoice_number} "
            f"for your order #{order.id}"
        )
        text = message.body.lower()
        assert "receipt" in text
        assert "invoice" not in text
        assert "τιμολ" not in text

    def test_the_greek_receipt_email_does_not_say_invoice_either(self):
        order = _order(language_code="el")

        with mydata_enabled(), FakeAade():
            OrderService.update_order_status(order, OrderStatus.SHIPPED)

        [message] = [
            m for m in mail.outbox if m.subject.startswith("Απόδειξη ")
        ]
        assert "τιμολόγι" not in message.body.lower()
        assert "τιμολόγι" not in message.subject.lower()
        assert "απόδειξη" in message.body.lower()

    def test_a_cash_on_delivery_order_is_issued_at_shipment_too(self):
        order = _order(
            lines=(("24", "50.00"), ("13", "30.00")),
            shipping="5.00",
            fee="2.00",
            paid_online=False,
        )

        with mydata_enabled(), FakeAade() as aade:
            OrderService.update_order_status(order, OrderStatus.SHIPPED)

        [receipt] = aade.receipts
        assert receipt.gross == Decimal("87.00")
        # Cash, as the courier collects it; one row, the whole total.
        assert receipt.payments == [(3, Decimal("87.00"))]
        # Goods, shipping and the fee follow the rates of the goods: both
        # rates are on the document.
        assert {line.category for line in receipt.lines} == {"1", "2"}

    def test_nothing_is_issued_before_the_order_ships(self):
        order = _order()

        with mydata_enabled(), FakeAade() as aade:
            generate_order_invoice(order.id)  # even if someone asks

        assert aade.sent == []
        assert not Invoice.objects.filter(order=order).exists()

    def test_cancelling_before_shipment_leaves_no_document(self):
        order = _order()

        with mydata_enabled(), FakeAade() as aade, FakeStripeRefunds():
            OrderService.cancel_order(
                order,
                "changed my mind",
                customer_initiated=False,
            )

        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED
        assert order.payment_status == PaymentStatus.REFUNDED
        # A refund was made, but there was never a receipt to credit.
        assert aade.sent == []
        assert not RetailCredit.objects.exists()
        assert not Invoice.objects.exists()

    def test_a_store_that_issues_by_hand_gets_nothing_automatic(self):
        order = _order()

        with mydata_enabled(auto_submit=False), FakeAade() as aade:
            OrderService.update_order_status(order, OrderStatus.SHIPPED)
            assert aade.sent == []
            # The staff action runs the same task.
            generate_order_invoice(order.id)

        assert len(aade.receipts) == 1

    def test_a_store_without_myDATA_issues_nothing(self):
        order = _order()

        with mydata_enabled(ready=False), FakeAade() as aade:
            OrderService.update_order_status(order, OrderStatus.SHIPPED)
            generate_order_invoice(order.id)

        assert aade.sent == []
        assert not Invoice.objects.filter(order=order).exists()

    def test_an_invoice_order_submits_nothing(self):
        """Staff issue the invoice in timologio: shipping or completing the
        order sends AADE nothing and creates no document."""
        order = _order(
            document_type=OrderDocumentTypeEnum.INVOICE,
            billing_vat_id="123456789",
            billing_company_name="Acme SA",
        )

        with mydata_enabled(), FakeAade() as aade, FakeStripeRefunds():
            OrderService.update_order_status(order, OrderStatus.SHIPPED)
            OrderService.update_order_status(order, OrderStatus.DELIVERED)
            OrderService.update_order_status(order, OrderStatus.COMPLETED)
            generate_order_invoice(order.id)
            OrderService.refund_order(order, amount=_eur("10.00"))

        assert aade.sent == []
        assert not Invoice.objects.exists()
        assert not RetailCredit.objects.exists()


class TestCreditForEveryRefundSource:
    def _shipped(self, **kwargs):
        order = _order(lines=(("24", "100.00"),), **kwargs)
        _receipt(order)
        return order

    def _assert_one_credit(self, order, aade, amount: str):
        [credit] = _credits(order)
        [sent] = aade.credits
        assert sent.invoice_type == "11.4"
        # AADE forbids correlatedInvoices on an 11.4 (error 205): the link to
        # the receipt is ours only.
        assert sent.correlated is None
        assert credit.invoice == Invoice.objects.get(order=order)
        assert sent.gross == Decimal(amount)
        assert sent.net + sent.vat == sent.gross
        assert credit.total == _eur(amount)
        assert credit.mydata_status == MyDataStatus.CONFIRMED
        assert credit.mydata_mark == aade.marks[-1]
        assert credit.reason == CreditReason.REFUND
        assert credit.refund is not None

    def test_the_in_app_refund(self):
        with mydata_enabled(), FakeAade() as aade, FakeStripeRefunds():
            order = self._shipped()
            OrderService.refund_order(order, reason="return")

        self._assert_one_credit(order, aade, "100.00")

    def test_the_staff_refund_action(self):
        staff = UserAccountFactory(is_staff=True, is_superuser=True)
        admin = OrderAdmin(Order, AdminSite())
        with mydata_enabled(), FakeAade() as aade, FakeStripeRefunds():
            order = self._shipped()
            # The action is offered where a return is handled.
            Order.objects.filter(pk=order.pk).update(
                status=OrderStatus.DELIVERED
            )
            request = RequestFactory().post(
                "/admin/order/order/",
                {
                    "_form_submitted": "true",
                    "amount": "30.00",
                    "reason": "One item came back",
                },
            )
            request.user = staff
            request.session = {}
            from django.contrib.messages.storage.fallback import (
                FallbackStorage,
            )

            request._messages = FallbackStorage(request)
            admin.refund_payment(request, object_id=order.pk)

        self._assert_one_credit(order, aade, "30.00")

    def test_a_stripe_dashboard_refund_webhook(self):
        event = Mock(spec=Event)
        event.id = "evt_dashboard_refund"
        event.type = "charge.refunded"
        event.data = {
            "object": {
                "id": "ch_x",
                "payment_intent": PI,
                "amount": 10000,
                "amount_refunded": 4000,
                "currency": "eur",
                "refunded": False,
            }
        }
        with mydata_enabled(), FakeAade() as aade:
            order = self._shipped()
            handle_stripe_charge_refunded(sender=None, event=event)
            # Stripe redelivers the event: nothing new on the ledger, so no
            # second document.
            handle_stripe_charge_refunded(sender=None, event=event)

        self._assert_one_credit(order, aade, "40.00")

    def test_a_viva_reversal_webhook(self):
        refund_id = REVERSAL_CREATED_EVENT["EventData"]["TransactionId"]
        payment_id = REVERSAL_CREATED_EVENT["EventData"]["ParentId"]
        order_code = REVERSAL_CREATED_EVENT["EventData"]["OrderCode"]
        viva = FakeVivaApi(
            transactions={
                payment_id: (
                    200,
                    retrieve_transaction(
                        amount=100.0, orderCode=order_code, statusId="R"
                    ),
                ),
                refund_id: (
                    200,
                    retrieve_refund(amount=25.0, order_code=order_code),
                ),
            }
        )
        with mydata_enabled(), FakeAade() as aade, viva:
            order = _order(
                lines=(("24", "100.00"),),
                pay_way=PayWayFactory.create_online_payment("viva_wallet"),
                payment_id=payment_id,
                payment_method="viva_wallet",
                metadata={"viva_order_codes": [str(order_code)]},
            )
            # The capture is Viva's, not the helper's Stripe row.
            order.payment_transactions.all().delete()
            PaymentTransactionFactory(
                order=order,
                provider="viva_wallet",
                provider_reference=payment_id,
                amount=_eur("100.00"),
            )
            _receipt(order)
            data = REVERSAL_CREATED_EVENT["EventData"]
            _handle_reversal_created(order, data, refund_id)
            _handle_reversal_created(order, data, refund_id)

        self._assert_one_credit(order, aade, "25.00")


class TestPartialCredits:
    def test_partial_credits_split_over_the_rates_and_sum_to_the_receipt(self):
        order = _order(
            lines=(("24", "60.00"), ("13", "40.00")),
            shipping="5.00",
        )
        refunds = ["30.00", "20.01", "54.99"]

        with mydata_enabled(), FakeAade() as aade, FakeStripeRefunds():
            invoice = _receipt(order)
            for amount in refunds[:2]:
                OrderService.refund_order(order, amount=_eur(amount))
            # Two partial credits, together below the receipt.
            assert sum(c.gross for c in aade.credits) == Decimal("50.01")
            assert sum(c.gross for c in aade.credits) < invoice.total.amount
            OrderService.refund_order(order, amount=_eur(refunds[2]))

        assert [c.gross for c in aade.credits] == [Decimal(a) for a in refunds]
        for credit in aade.credits:
            assert credit.net + credit.vat == credit.gross
            # A mixed order credits both rates, never one of them.
            assert {line.category for line in credit.lines} == {"1", "2"}
        # Never more than the receipt's net at any point (our cap is the only
        # guard: AADE cannot link an 11.4 to its receipt)
        # and exactly the receipt's net and VAT once everything is back.
        running = Decimal(0)
        for credit in aade.credits:
            running += credit.net
            assert running <= invoice.subtotal.amount
        assert sum(c.net for c in aade.credits) == invoice.subtotal.amount
        assert sum(c.vat for c in aade.credits) == invoice.total_vat.amount
        assert [c.reason for c in _credits(order)] == [CreditReason.REFUND] * 3

    def test_a_credit_past_the_receipt_is_never_sent(self):
        order = _order(lines=(("24", "100.00"),))
        with mydata_enabled(), FakeAade() as aade:
            invoice = _receipt(order)
            # The ledger says 150 came back, more than the receipt holds.
            charge = order.payment_transactions.get()
            row = PaymentTransactionFactory(
                order=order,
                kind=PaymentTransactionKind.REFUND,
                charge=charge,
                amount=_eur("150.00"),
                provider_reference="re_too_much",
            )
            row.__class__.objects.filter(pk=row.pk).update(
                created_at=invoice.created_at
            )
            assert create_pending_credits(order.id) == []

        assert aade.credits == []
        assert not RetailCredit.objects.exists()
        assert order.history.filter(
            new_value__note__icontains="No credit document"
        ).exists()

    def test_cumulative_net_never_passes_the_receipt_with_awkward_cents(self):
        """Credits of 3.33, 3.33, 3.33 and 0.01 against 10.00 at 24%
        (receipt net 8.06): rounding each credit's net on its own would
        credit 8.08, past the receipt, and nothing at AADE would catch it. The net is derived from the
        cumulative gross, so it ends exactly on 8.06."""
        receipt = [
            {
                "rate": "24.00",
                "subtotal": "8.06",
                "vat": "1.94",
                "gross": "10.00",
            }
        ]
        prior: list[list[dict[str, str]]] = []
        nets = []
        for amount in ("3.33", "3.33", "3.33", "0.01"):
            rows = credit_breakdown(receipt, prior, Decimal(amount))
            prior.append(rows)
            nets.append(sum(Decimal(r["subtotal"]) for r in rows))
            assert sum(nets) <= Decimal("8.06")
        assert sum(nets) == Decimal("8.06")
        assert sum(
            Decimal(r["vat"]) for rows in prior for r in rows
        ) == Decimal("1.94")

    def test_credit_breakdown_refuses_more_than_the_receipt_holds(self):
        receipt = [
            {
                "rate": "24.00",
                "subtotal": "8.06",
                "vat": "1.94",
                "gross": "10.00",
            }
        ]
        first = credit_breakdown(receipt, [], Decimal("9.00"))
        with pytest.raises(CreditExceedsReceiptError):
            credit_breakdown(receipt, [first], Decimal("1.01"))


class TestNoCreditWithoutAReceipt:
    def test_a_refund_before_the_receipt_needs_no_document(self):
        order = _order(lines=(("24", "100.00"),))
        with mydata_enabled(), FakeAade() as aade, FakeStripeRefunds():
            OrderService.refund_order(order, amount=_eur("10.00"))
            _receipt(order)
            create_pending_credits(order.id)

        assert len(aade.receipts) == 1
        assert aade.credits == []
        assert not RetailCredit.objects.exists()

    def test_a_refund_waits_for_the_receipt_to_be_registered(self):
        order = _order(lines=(("24", "100.00"),))
        with mydata_enabled(), FakeAade() as aade, FakeStripeRefunds():
            _mark_shipped(order)
            # The receipt row exists; AADE has not registered it yet.
            generate_invoice(order)
            OrderService.refund_order(order, amount=_eur("40.00"))
            assert aade.sent == []
            assert not RetailCredit.objects.exists()

            # Registered: the refund that waited gets its document.
            send_invoice_to_mydata(order.id)

        [receipt] = aade.receipts
        [credit] = aade.credits
        assert credit.correlated is None
        assert credit.gross == Decimal("40.00")
        assert receipt.gross == Decimal("100.00")

    def test_issuing_credits_twice_creates_one(self):
        order = _order(lines=(("24", "100.00"),))
        with mydata_enabled(), FakeAade() as aade, FakeStripeRefunds():
            _receipt(order)
            OrderService.refund_order(order, amount=_eur("40.00"))
            assert create_pending_credits(order.id) == []
            assert create_pending_credits(order.id) == []

        assert len(aade.credits) == 1
        assert _credits(order).count() == 1


class TestOrderClosedWithoutASale:
    def test_a_returned_cash_on_delivery_parcel_is_credited_in_full(self):
        order = _order(
            lines=(("24", "50.00"), ("13", "30.00")),
            shipping="5.00",
            paid_online=False,
        )
        with mydata_enabled(), FakeAade() as aade:
            invoice = _receipt(order)
            # The courier brings the refused parcel back: the unpaid payment
            # settles to CANCELED and the sale never happened.
            OrderService.update_order_status(order, OrderStatus.RETURNED)

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.CANCELED
        [sent] = aade.credits
        assert sent.gross == invoice.total.amount == Decimal("85.00")
        assert sent.net == invoice.subtotal.amount
        assert sent.correlated is None
        assert _credits(order).get().invoice == invoice
        [credit] = _credits(order)
        assert credit.reason == CreditReason.SETTLEMENT
        assert credit.refund is None
        # Idempotent: the order closing again, or staff pressing the
        # button, issues nothing more.
        with mydata_enabled(), FakeAade() as again:
            create_pending_credits(order.id)
        assert again.sent == []

    def test_a_fully_refunded_order_also_credits_the_gift_card_share(self):
        """The receipt covers the whole sale; the provider only refunds the
        card's share. The rest (returned to the gift card) is credited by
        the settlement, so the credits add up to the receipt."""
        order = _order(lines=(("24", "105.00"),), paid_online=True)
        # 5.00 of the 105.00 was paid with a gift card: the capture is 100.
        Order.objects.filter(pk=order.pk).update(gift_card_amount=_eur("5.00"))
        charge = order.payment_transactions.get()
        charge.__class__.objects.filter(pk=charge.pk).update(
            amount=_eur("100.00")
        )
        with mydata_enabled(), FakeAade() as aade, FakeStripeRefunds():
            invoice = _receipt(order)
            OrderService.refund_order(order, reason="return")

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.REFUNDED
        assert sorted(c.gross for c in aade.credits) == [
            Decimal("5.00"),
            Decimal("100.00"),
        ]
        assert sum(c.gross for c in aade.credits) == invoice.total.amount
        assert sum(c.net for c in aade.credits) == invoice.subtotal.amount
        assert {c.reason for c in _credits(order)} == {
            CreditReason.REFUND,
            CreditReason.SETTLEMENT,
        }

    def test_a_returned_paid_order_refunded_in_full_is_credited_in_full(self):
        """After shipment an order is not cancelled but returned, and the
        refund that follows credits the whole receipt."""
        order = _order(lines=(("24", "100.00"),))
        with mydata_enabled(), FakeAade() as aade, FakeStripeRefunds():
            invoice = _receipt(order)
            OrderService.update_order_status(order, OrderStatus.RETURNED)
            OrderService.refund_order(order, reason="returned")

        assert sum(c.gross for c in aade.credits) == invoice.total.amount
        assert all(c.correlated is None for c in aade.credits)
        assert {c.invoice for c in _credits(order)} == {invoice}


class TestDocuments:
    def test_the_credit_pdf_is_a_credit_receipt_not_an_invoice(self):
        order = _order(lines=(("24", "100.00"),))
        captured: list[dict] = []

        def fake_render(context):
            captured.append(context)
            return b"%PDF-1.4 credit"

        with mydata_enabled(), FakeAade(), FakeStripeRefunds():
            _receipt(order)
            OrderService.refund_order(order, amount=_eur("40.00"))
            credit = _credits(order).get()
            with patch(
                "order.invoicing._render_pdf_bytes", side_effect=fake_render
            ):
                render_credit_pdf(credit)

        [context] = captured
        html = render_to_string("invoices/invoice.html", context)
        assert "RETAIL CREDIT RECEIPT" in html
        assert credit.credit_number in html
        assert str(credit.mydata_mark) in html
        assert "INVOICE" not in html
        assert credit.has_document()
        assert credit.document_file.name.startswith("invoices/")

    def test_the_receipt_pdf_is_a_receipt_not_an_invoice(self):
        order = _order(lines=(("24", "100.00"),))
        captured: list[dict] = []

        def fake_render(context):
            captured.append(context)
            return b"%PDF-1.4 receipt"

        _mark_shipped(order)
        with (
            mydata_enabled(auto_submit=False),
            patch("order.invoicing._render_pdf_bytes", side_effect=fake_render),
        ):
            generate_invoice(order)

        html = render_to_string("invoices/invoice.html", captured[0])
        assert "RETAIL RECEIPT" in html
        assert "INVOICE" not in html
        assert "Bill to" not in html


class TestInvoiceOrdersInTheAdmin:
    def _admin(self):
        return OrderAdmin(Order, AdminSite())

    def test_staff_see_what_they_need_to_issue_the_invoice(self):
        order = _order(
            lines=(("24", "62.00"), ("13", "11.30")),
            shipping="5.00",
            document_type=OrderDocumentTypeEnum.INVOICE,
            billing_vat_id="123456789",
            billing_company_name="Acme SA",
            billing_tax_office="Athens A",
            billing_activity="Retail",
            billing_street="Main",
            billing_street_number="5",
            billing_city="Athens",
            billing_zipcode="10431",
            billing_country="GR",
        )

        summary = str(self._admin().timologio_summary(order))

        for needle in (
            "Acme SA",
            "123456789",
            "Athens A",
            "Retail",
            "Main 5",
            "10431 Athens",
            "24",
            "13",
            "78.30",
        ):
            assert needle in summary

    def test_a_receipt_order_has_nothing_to_issue_by_hand(self):
        order = _order()
        assert self._admin().timologio_summary(order) == "—"

    def test_the_timologio_marks_are_recorded_on_the_order(self):
        order = _order(document_type=OrderDocumentTypeEnum.INVOICE)
        admin = self._admin()
        request = RequestFactory().get("/")
        request.user = UserAccountFactory(is_staff=True, is_superuser=True)
        fields = admin.get_fields(request, order)
        for name in (
            "timologio_invoice_number",
            "timologio_invoice_mark",
            "timologio_credit_number",
            "timologio_credit_mark",
        ):
            assert name in fields
            assert name not in admin.get_readonly_fields(request, order)

        Order.objects.filter(pk=order.pk).update(
            timologio_invoice_number="Α-123",
            timologio_invoice_mark=400001234567890,
            timologio_credit_number="Π-7",
            timologio_credit_mark=400001234567999,
        )
        order.refresh_from_db()
        assert order.timologio_invoice_mark == 400001234567890
        assert order.timologio_credit_mark == 400001234567999
        card = admin._document_card(order, None, platform_issues=False)
        assert card["value"] == "Α-123"
        assert "timologio" in str(card["footer"])

    def test_a_receipt_orders_card_shows_the_registered_receipt(self):
        order = _order()
        with mydata_enabled(), FakeAade():
            invoice = _receipt(order)
        card = self._admin()._document_card(
            order, invoice, platform_issues=True
        )
        assert card["value"] == invoice.invoice_number


class TestReceiptNetOfRefundsMadeBeforeIt:
    """A retail receipt documents the sale for the consideration actually
    paid. Money returned BEFORE it is issued reduces it, and no credit
    document follows for that money; a credit (11.4) only corrects a
    receipt that exists. Money returned after it gets a credit as ever."""

    @pytest.fixture(autouse=True)
    def _stripe(self):
        # One Stripe for the test, so each refund gets its own id.
        with FakeStripeRefunds():
            yield

    def _refund(self, order, amount=None):
        OrderService.refund_order(
            order, amount=_eur(amount) if amount else None
        )

    def test_the_receipt_is_for_the_total_less_the_refund(self):
        order = _order(lines=(("24", "100.00"),))
        self._refund(order, "30.00")

        with mydata_enabled(), FakeAade() as aade:
            OrderService.update_order_status(order, OrderStatus.SHIPPED)

        [receipt] = aade.receipts
        assert receipt.gross == Decimal("70.00")
        invoice = Invoice.objects.get(order=order)
        assert invoice.total == _eur("70.00")
        # The full 100.00 is 80.65 + 19.35; the 30.00 returned is 24.19 +
        # 5.81 (the credit rule), so what is left is 56.46 + 13.54.
        [row] = invoice.vat_breakdown
        assert Decimal(row["rate"]) == 24
        assert (row["subtotal"], row["vat"], row["gross"]) == (
            "56.46",
            "13.54",
            "70.00",
        )
        assert aade.credits == []
        assert not _credits(order).exists()

    def test_it_is_split_over_the_rates_as_a_credit_would_be(self):
        order = _order(lines=(("24", "124.00"), ("13", "113.00")))
        self._refund(order, "59.25")

        with mydata_enabled(), FakeAade() as aade:
            OrderService.update_order_status(order, OrderStatus.SHIPPED)

        invoice = Invoice.objects.get(order=order)
        rows = {Decimal(r["rate"]): r for r in invoice.vat_breakdown}
        # 59.25 of 237.00: proportional to what each rate holds (25%).
        assert Decimal(rows[Decimal(24)]["gross"]) == Decimal("93.00")
        assert Decimal(rows[Decimal(13)]["gross"]) == Decimal("84.75")
        assert invoice.total == _eur("177.75")
        [receipt] = aade.receipts
        assert receipt.gross == Decimal("177.75")
        for row in invoice.vat_breakdown:
            assert Decimal(row["subtotal"]) + Decimal(row["vat"]) == Decimal(
                row["gross"]
            )

    def test_a_refund_after_the_receipt_gets_a_credit(self):
        order = _order(lines=(("24", "100.00"),))
        self._refund(order, "30.00")

        with mydata_enabled(), FakeAade() as aade:
            invoice = _receipt(order)
            assert invoice.total == _eur("70.00")
            self._refund(order, "20.00")

        [credit] = _credits(order)
        assert credit.total == _eur("20.00")
        assert [c.gross for c in aade.credits] == [Decimal("20.00")]

    def test_a_refund_before_never_produces_a_credit(self):
        order = _order(lines=(("24", "100.00"),))
        self._refund(order, "30.00")
        self._refund(order, "10.00")

        with mydata_enabled(), FakeAade() as aade:
            _receipt(order)
            create_pending_credits(order.id)

        assert not _credits(order).exists()
        assert aade.credits == []

    def test_the_value_returned_onto_a_gift_card_counts_too(self):
        from giftcard.factories import GiftCardFactory
        from giftcard.models import (
            GiftCardTransaction,
            GiftCardTransactionKind,
        )

        order = _order(lines=(("24", "100.00"),), paid_online=True)
        Order.objects.filter(pk=order.pk).update(
            gift_card_amount=_eur("40.00"), amount_due=_eur("60.00")
        )
        order.refresh_from_db()
        PaymentTransaction = order.payment_transactions.get()
        PaymentTransaction.amount = _eur("60.00")
        PaymentTransaction.save(update_fields=["amount"])
        card = GiftCardFactory(initial_value=_eur("40.00"))
        GiftCardTransaction.objects.create(
            gift_card=card,
            kind=GiftCardTransactionKind.REDEEM,
            amount=Decimal("-40.00"),
            order=order,
        )
        # 60 card + 40 gift card; a EUR 100 return gives both back.
        self._refund(order, "70.00")

        from order.invoicing import refunded_before_receipt

        assert refunded_before_receipt(order) == Decimal("70.00")
        with mydata_enabled(), FakeAade() as aade:
            OrderService.update_order_status(order, OrderStatus.SHIPPED)
        [receipt] = aade.receipts
        assert receipt.gross == Decimal("30.00")

    def test_a_chargeback_before_the_receipt_does_not_reduce_it(self):
        order = _order(lines=(("24", "100.00"),))
        charge = order.payment_transactions.get()
        PaymentTransactionFactory(
            order=order,
            charge=charge,
            kind=PaymentTransactionKind.CHARGEBACK,
            amount=_eur("30.00"),
            provider_reference="dp_before",
        )

        with mydata_enabled(), FakeAade() as aade:
            OrderService.update_order_status(order, OrderStatus.SHIPPED)

        [receipt] = aade.receipts
        assert receipt.gross == Decimal("100.00")
        assert aade.credits == []

    def test_a_refund_that_failed_does_not_reduce_it(self):
        from order.payment_ledger import PaymentLedger

        order = _order(lines=(("24", "100.00"),))
        charge = order.payment_transactions.get()
        PaymentLedger.record_refund(
            order, charge=charge, amount=_eur("30.00"), reference="re_1"
        )
        PaymentLedger.record_refund_reversal(
            order, charge=charge, amount=_eur("30.00"), reference="re_1"
        )

        with mydata_enabled(), FakeAade() as aade:
            OrderService.update_order_status(order, OrderStatus.SHIPPED)

        [receipt] = aade.receipts
        assert receipt.gross == Decimal("100.00")

    def test_everything_refunded_before_it_means_no_receipt(self):
        from order.invoicing import ReceiptNotIssuable

        order = _order(lines=(("24", "100.00"),))
        self._refund(order)
        _mark_shipped(order)

        with mydata_enabled(), FakeAade() as aade:
            with pytest.raises(ReceiptNotIssuable, match="refunded in full"):
                generate_invoice(order)
            generate_order_invoice(order.id)

        assert not Invoice.objects.filter(order=order).exists()
        assert aade.sent == []

    def test_a_receipt_that_exists_is_unaffected_by_a_later_full_refund(self):
        order = _order(lines=(("24", "100.00"),))
        with mydata_enabled(), FakeAade():
            invoice = _receipt(order)
            self._refund(order)

            assert generate_invoice(order).pk == invoice.pk
        assert Invoice.objects.get(order=order).total == _eur("100.00")


class TestTheReceiptRowIsTheCutOff:
    """ONE cut-off moment: the receipt row's creation. A new receipt nets
    what was returned before it; a row that exists keeps its figures and is
    never recomputed; credits cover what came after."""

    @pytest.fixture(autouse=True)
    def _stripe(self):
        with FakeStripeRefunds():
            yield

    def _failed_render(self, order):
        """Issue the receipt with a render that dies after the row was
        committed (the row exists, with no document)."""
        _mark_shipped(order)
        with (
            mydata_enabled(),
            patch(
                "order.invoicing._render_pdf_bytes",
                side_effect=RuntimeError("x"),
            ),
            pytest.raises(RuntimeError),
        ):
            generate_invoice(order)
        invoice = Invoice.objects.get(order=order)
        assert not invoice.has_document()
        return invoice

    def test_a_refund_after_a_failed_render_is_credited_not_netted(self):
        order = _order(lines=(("24", "100.00"),))
        OrderService.refund_order(order, amount=_eur("30.00"))
        invoice = self._failed_render(order)
        assert invoice.total == _eur("70.00")
        OrderService.refund_order(order, amount=_eur("20.00"))

        with mydata_enabled(), FakeAade() as aade:
            generate_invoice(order)
            send_invoice_to_mydata(order.id)

        invoice.refresh_from_db()
        assert invoice.total == _eur("70.00")
        [receipt] = aade.receipts
        assert receipt.gross == Decimal("70.00")
        [credit] = _credits(order)
        assert credit.total == _eur("20.00")
        assert [c.gross for c in aade.credits] == [Decimal("20.00")]

    def test_force_on_an_unregistered_row_behaves_the_same(self):
        order = _order(lines=(("24", "100.00"),))
        OrderService.refund_order(order, amount=_eur("30.00"))
        with mydata_enabled(), FakeAade():
            _mark_shipped(order)
            generate_invoice(order)
            OrderService.refund_order(order, amount=_eur("20.00"))

            invoice = generate_invoice(order, force=True)

            assert invoice.total == _eur("70.00")
            send_invoice_to_mydata(order.id)
            assert [c.total for c in _credits(order)] == [_eur("20.00")]

    def test_a_full_refund_before_a_retried_row_is_refused(self):
        """A row left by an older release, issued for the full total, whose
        order was then refunded in full before the retry: no zero receipt."""
        from order.invoicing import ReceiptNotIssuable

        order = _order(lines=(("24", "100.00"),))
        invoice = self._failed_render(order)
        OrderService.refund_order(order)
        # The row is later than the refund (an older release's row).
        Invoice.objects.filter(pk=invoice.pk).update(
            created_at=invoice.created_at + timedelta(days=1)
        )

        with mydata_enabled(), FakeAade() as aade:
            with pytest.raises(ReceiptNotIssuable, match="refunded in full"):
                generate_invoice(order)
            with pytest.raises(ReceiptNotIssuable, match="refunded in full"):
                generate_invoice(order, force=True)

        assert aade.sent == []

    def test_a_refund_after_the_row_never_triggers_the_guard(self):
        order = _order(lines=(("24", "100.00"),))
        self._failed_render(order)
        OrderService.refund_order(order)

        with mydata_enabled(), FakeAade() as aade:
            generate_invoice(order)
            send_invoice_to_mydata(order.id)

        [receipt] = aade.receipts
        assert receipt.gross == Decimal("100.00")
        assert [c.total for c in _credits(order)] == [_eur("100.00")]

    def test_a_registered_receipt_is_frozen(self):
        order = _order(lines=(("24", "100.00"),))
        with mydata_enabled(), FakeAade():
            invoice = _receipt(order)
            OrderService.refund_order(order)

            assert generate_invoice(order, force=True).total == _eur("100.00")
        assert invoice.mydata_mark


class TestCreditForValueReturnedOntoGiftCardsAndPoints:
    """Value that goes back onto gift cards or points AFTER the receipt
    exists reverses the sale as a provider refund does: one retail credit
    (11.4) for it, through the same path. Before the receipt it was netted
    (no credit); a chargeback never has one."""

    @pytest.fixture(autouse=True)
    def _stripe(self):
        with FakeStripeRefunds():
            yield

    def _gift_card_order(self, *, card="60.00", gift="40.00"):
        from giftcard.factories import GiftCardFactory
        from giftcard.models import (
            GiftCardTransaction,
            GiftCardTransactionKind,
        )

        order = _order(lines=(("24", "100.00"),))
        Order.objects.filter(pk=order.pk).update(
            gift_card_amount=_eur(gift), amount_due=_eur(card)
        )
        order.refresh_from_db()
        charge = order.payment_transactions.get()
        if Decimal(card) > 0:
            charge.amount = _eur(card)
            charge.save(update_fields=["amount"])
        else:
            # Paid entirely with the gift card: no provider payment.
            charge.delete()
            Order.objects.filter(pk=order.pk).update(
                pay_way=None, payment_id=""
            )
            order.refresh_from_db()
        GiftCardTransaction.objects.create(
            gift_card=GiftCardFactory(initial_value=_eur(gift)),
            kind=GiftCardTransactionKind.REDEEM,
            amount=-Decimal(gift),
            order=order,
        )
        return order

    def test_a_gift_card_return_after_the_receipt_makes_one_credit(self):
        order = self._gift_card_order(card="0.00", gift="100.00")
        with mydata_enabled(), FakeAade() as aade:
            _receipt(order)
            OrderService.refund_order(order, amount=_eur("40.00"))

        [credit] = _credits(order)
        assert credit.total == _eur("40.00")
        assert credit.refund is None
        assert credit.refund_attempt is not None
        assert [c.gross for c in aade.credits] == [Decimal("40.00")]

    def test_a_mixed_refund_credits_the_whole_returned_value(self):
        order = self._gift_card_order()
        with mydata_enabled(), FakeAade() as aade:
            _receipt(order)
            OrderService.refund_order(order, amount=_eur("75.00"))

        issued = list(_credits(order))
        assert sum((c.total.amount for c in issued), Decimal(0)) == Decimal(
            "75.00"
        )
        assert sorted(c.total.amount for c in issued) == [
            Decimal("15.00"),
            Decimal("60.00"),
        ]
        assert sum((c.gross for c in aade.credits), Decimal(0)) == Decimal(
            "75.00"
        )
        for credit in issued:
            assert credit.mydata_status == MyDataStatus.CONFIRMED

    def test_a_return_before_the_receipt_makes_no_credit(self):
        order = self._gift_card_order()
        OrderService.refund_order(order, amount=_eur("75.00"))

        with mydata_enabled(), FakeAade() as aade:
            invoice = _receipt(order)
            create_pending_credits(order.id)

        assert invoice.total == _eur("25.00")
        assert not _credits(order).exists()
        assert aade.credits == []

    def test_the_credits_never_exceed_the_receipt(self):
        order = self._gift_card_order()
        with mydata_enabled(), FakeAade() as aade:
            invoice = _receipt(order)
            OrderService.refund_order(order, amount=_eur("60.00"))
            OrderService.refund_order(order)
            create_pending_credits(order.id)

        total = sum((c.total.amount for c in _credits(order)), Decimal(0))
        assert total == invoice.total.amount == Decimal("100.00")
        assert sum((c.gross for c in aade.credits), Decimal(0)) == total

    def test_a_repeat_makes_no_second_credit(self):
        order = self._gift_card_order(card="0.00", gift="100.00")
        with mydata_enabled(), FakeAade() as aade:
            _receipt(order)
            OrderService.refund_order(order, amount=_eur("40.00"))
            create_pending_credits(order.id)
            create_pending_credits(order.id)

        assert _credits(order).count() == 1
        assert len(aade.credits) == 1

    def test_a_return_made_before_the_cut_off_is_not_credited(self):
        """Value returned before the receipt row existed was netted; its
        attempt must not be credited by a later reconciliation."""
        order = self._gift_card_order()
        OrderService.refund_order(order, amount=_eur("75.00"))
        with mydata_enabled(), FakeAade():
            _receipt(order)
            create_pending_credits(order.id)

        assert not _credits(order).exists()

    def test_the_credit_is_for_what_went_back_not_what_was_claimed(self):
        from giftcard.models import (
            GiftCardTransaction,
            GiftCardTransactionKind,
        )
        from order.models.refund_attempt import (
            RefundAttempt,
            RefundAttemptKind,
            RefundAttemptStatus,
        )

        order = self._gift_card_order(card="0.00", gift="100.00")
        with mydata_enabled(), FakeAade():
            _receipt(order)
            attempt = RefundAttempt.objects.create(
                order=order,
                kind=RefundAttemptKind.STORED_VALUE,
                charge=None,
                amount=_eur("50.00"),
                status=RefundAttemptStatus.SETTLED,
            )
            redeem = GiftCardTransaction.objects.get(
                order=order, kind=GiftCardTransactionKind.REDEEM
            )
            # A race returned most of the claim elsewhere: only 20 went back
            # under this attempt.
            GiftCardTransaction.objects.create(
                gift_card=redeem.gift_card,
                kind=GiftCardTransactionKind.REFUND_CREDIT,
                amount=Decimal("20.00"),
                order=order,
                refund_attempt=attempt,
            )
            create_pending_credits(order.id)

        [credit] = _credits(order)
        assert credit.total == _eur("20.00")
