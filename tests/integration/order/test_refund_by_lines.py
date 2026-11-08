"""Refunding order lines: the amount from the frozen figures, and the myDATA
11.4 credit that carries exactly those lines' VAT rates.

Before this a refund was an amount, and its credit document spread it over
the receipt's rates in proportion, so returning one 13% item of a 24%/13%
order credited both rates. A refund by lines
(``OrderService.refund_order(lines=..., include_shipping=...)``) keeps the
lines on its refund attempts and the credit follows them. A bare amount is
a goodwill refund and stays in proportion.

Only the boundaries are faked: AADE (``FakeAade``), Stripe
(``FakeStripeRefunds``) and Viva (``FakeVivaApi``).
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
import stripe
from django.contrib.admin.sites import AdminSite
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory
from djmoney.money import Money

from giftcard.factories import GiftCardFactory
from giftcard.models import GiftCardTransaction, GiftCardTransactionKind
from order.admin import OrderAdmin, ReturnOrderForm
from order.credit_documents import CreditExceedsReceiptError, credit_breakdown
from order.enum.document_type import OrderDocumentTypeEnum
from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.exceptions import PaymentError
from order.factories import OrderFactory, PaymentTransactionFactory
from order.factories.item import OrderItemFactory
from order.invoicing import generate_invoice
from order.models import Invoice, Order, OrderHistory, RetailCredit
from order.models.invoice import MyDataStatus
from order.models.refund_attempt import (
    RefundAttempt,
    RefundAttemptLine,
    RefundAttemptStatus,
    RefundReason,
)
from order.payment_ledger import PaymentLedger
from order.refund_lines import RefundPiece, split_over_attempts
from order.services import OrderService
from order.tasks import send_invoice_to_mydata
from order.taxation import ChargeKind
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory
from tests.utils.mydata import FakeAade, mydata_enabled
from tests.utils.orders import courier_cash_order
from tests.utils.stripe_refunds import FakeStripeRefunds
from tests.utils.viva import CANCEL_SUCCESS, FakeVivaApi
from user.factories import UserAccountFactory
from vat.factories import VatFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

PI = "pi_lines"
VIVA_PAYMENT = "b1a3067c-321b-4ec6-bc9d-1778aef2a19d"


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


def _eur(amount: str | Decimal) -> Money:
    return Money(Decimal(amount), "EUR")


def _order(
    *,
    lines=(("24", "60.00", 1), ("13", "40.00", 1)),
    shipping="0.00",
    provider="stripe",
    paid_online=True,
    gift="0.00",
    **kwargs,
):
    """An order with one line per ``(vat rate, unit price, quantity)``,
    paid in full: online (the capture on the ledger) or on delivery."""
    goods = sum(Decimal(price) * qty for _rate, price, qty in lines)
    total = goods + Decimal(shipping)
    paid = total - Decimal(gift)
    kwargs.setdefault("document_type", OrderDocumentTypeEnum.RECEIPT)
    kwargs.setdefault("status", OrderStatus.DELIVERED)
    reference = kwargs.get("payment_id") or (
        PI if provider == "stripe" else VIVA_PAYMENT
    )
    if paid_online:
        kwargs.setdefault(
            "pay_way", PayWayFactory.create_online_payment(provider)
        )
        kwargs.setdefault("payment_status", PaymentStatus.COMPLETED)
        kwargs.setdefault("payment_id", reference)
        kwargs.setdefault("payment_method", provider)
    else:
        kwargs.setdefault("payment_status", PaymentStatus.COMPLETED)
        kwargs.setdefault("payment_id", "")
        kwargs.setdefault("payment_method", "")
    order = (
        OrderFactory(num_order_items=0, **kwargs)
        if paid_online
        else courier_cash_order(**kwargs)
    )
    Order.objects.filter(pk=order.pk).update(
        shipping_price=_eur(shipping),
        payment_method_fee=_eur("0.00"),
        amount_due=_eur(paid),
        gift_card_amount=_eur(gift),
    )
    for rate, price, quantity in lines:
        OrderItemFactory(
            order=order,
            product=ProductFactory(vat=VatFactory(value=Decimal(rate))),
            price=_eur(price),
            quantity=quantity,
        )
    if paid_online:
        PaymentTransactionFactory(
            order=order,
            provider=provider,
            amount=_eur(paid),
            provider_reference=reference,
        )
    else:
        PaymentTransactionFactory(
            order=order,
            kind=PaymentTransactionKind.COD_COLLECTION,
            provider="acs_cod",
            provider_reference=f"VOUCHER-{order.pk}",
            amount=_eur(paid),
        )
    if Decimal(gift) > 0:
        card = GiftCardFactory(initial_value=_eur(gift))
        GiftCardTransaction.objects.create(
            gift_card=card,
            kind=GiftCardTransactionKind.REDEEM,
            amount=-Decimal(gift),
            order=order,
        )
    order.refresh_from_db()
    return order


def _item(order, rate: str):
    return order.items.get(vat_rate=Decimal(rate))


def _receipt(order) -> Invoice:
    generate_invoice(order)
    send_invoice_to_mydata(order.id)
    invoice = Invoice.objects.get(order=order)
    assert invoice.mydata_status == MyDataStatus.CONFIRMED
    return invoice


def _rates(credit) -> dict[str, Decimal]:
    """The gross a sent credit holds per myDATA VAT category (1 is 24%,
    2 is 13%)."""
    gross: dict[str, Decimal] = {}
    for line in credit.lines:
        gross[line.category] = gross.get(line.category, Decimal(0)) + (
            line.net + line.vat
        )
    return gross


CAT_24, CAT_13 = "1", "2"


class TestAReturnedItemIsCreditedAtItsOwnRate:
    def test_one_13_percent_item_of_a_mixed_order_credits_13_only(self):
        order = _order()

        with mydata_enabled(), FakeAade() as aade, FakeStripeRefunds():
            invoice = _receipt(order)
            result = OrderService.refund_order(
                order,
                lines=[(_item(order, "13"), 1)],
                reason_code=RefundReason.RETURNED,
            )

        assert result.complete
        assert result.value_returned == _eur("40.00")
        [credit] = aade.credits
        assert credit.invoice_type == "11.4"
        assert credit.correlated is None
        assert _rates(credit) == {CAT_13: Decimal("40.00")}
        assert credit.net == Decimal("35.40")
        assert credit.vat == Decimal("4.60")
        [stored] = RetailCredit.objects.filter(order=order)
        assert [Decimal(row["rate"]) for row in stored.vat_breakdown] == [
            Decimal(13)
        ]
        assert stored.invoice == invoice

    def test_the_24_percent_item_credits_24_only(self):
        order = _order()

        with mydata_enabled(), FakeAade() as aade, FakeStripeRefunds():
            _receipt(order)
            OrderService.refund_order(
                order,
                lines=[(_item(order, "24"), 1)],
                reason_code=RefundReason.DAMAGED,
            )

        [credit] = aade.credits
        assert _rates(credit) == {CAT_24: Decimal("60.00")}

    def test_both_items_back_credit_the_whole_receipt_exactly(self):
        order = _order()

        with mydata_enabled(), FakeAade() as aade, FakeStripeRefunds():
            invoice = _receipt(order)
            OrderService.refund_order(
                order,
                lines=[(_item(order, "24"), 1), (_item(order, "13"), 1)],
                reason_code=RefundReason.RETURNED,
            )

        [credit] = aade.credits
        assert _rates(credit) == {
            CAT_24: Decimal("60.00"),
            CAT_13: Decimal("40.00"),
        }
        assert credit.net == invoice.subtotal.amount
        assert credit.vat == invoice.total_vat.amount
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.REFUNDED

    def test_a_bare_amount_stays_in_proportion_over_the_rates(self):
        order = _order()

        with mydata_enabled(), FakeAade() as aade, FakeStripeRefunds():
            _receipt(order)
            OrderService.refund_order(order, amount=_eur("40.00"))

        [credit] = aade.credits
        # A goodwill refund names no line, so both rates share it.
        assert _rates(credit) == {
            CAT_24: Decimal("24.00"),
            CAT_13: Decimal("16.00"),
        }


class TestPartialQuantities:
    def test_units_are_priced_cumulatively_and_add_up_to_the_line(self):
        # 3 x 33.34 = 100.02, refunded one unit at a time.
        order = _order(lines=(("13", "33.34", 3),))
        item = _item(order, "13")

        with FakeStripeRefunds():
            amounts = [
                OrderService.refund_order(
                    order, lines=[(item, 1)]
                ).value_returned.amount
                for _ in range(3)
            ]

        assert amounts == [Decimal("33.34")] * 3
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.REFUNDED

    def test_a_discounted_line_refunds_what_was_paid_for_it(self):
        order = _order(lines=(("13", "10.00", 3), ("24", "20.00", 1)))
        item = _item(order, "13")
        # 6.00 off the 30.00 line, frozen on it: 24.00 was paid for it.
        item.discount_allocation = _eur("6.00")
        item.save(update_fields=["discount_allocation"])
        Order.objects.filter(pk=order.pk).update(
            discount_amount=_eur("6.00"), amount_due=_eur("44.00")
        )
        order = Order.objects.get(pk=order.pk)

        with FakeStripeRefunds():
            first = OrderService.refund_order(order, lines=[(item, 1)])
            rest = OrderService.refund_order(order, lines=[(item, 2)])

        assert first.value_returned.amount == Decimal("8.00")
        assert rest.value_returned.amount == Decimal("16.00")

    def test_a_partial_refund_credits_the_units_at_the_lines_rate(self):
        order = _order(lines=(("24", "60.00", 1), ("13", "20.00", 2)))

        with mydata_enabled(), FakeAade() as aade, FakeStripeRefunds():
            _receipt(order)
            OrderService.refund_order(order, lines=[(_item(order, "13"), 1)])

        [credit] = aade.credits
        assert _rates(credit) == {CAT_13: Decimal("20.00")}


class TestShipping:
    def test_shipping_is_split_over_the_rates_the_receipt_splits_it(self):
        order = _order(shipping="10.00")

        with mydata_enabled(), FakeAade() as aade, FakeStripeRefunds():
            _receipt(order)
            result = OrderService.refund_order(
                order,
                lines=[(_item(order, "13"), 1)],
                include_shipping=True,
            )

        assert result.value_returned == _eur("50.00")
        [credit] = aade.credits
        # 10.00 of shipping follows the goods: 6.00 at 24%, 4.00 at 13%.
        assert _rates(credit) == {
            CAT_13: Decimal("44.00"),
            CAT_24: Decimal("6.00"),
        }

    def test_without_shipping_it_stays_with_the_customer(self):
        order = _order(shipping="10.00")

        with mydata_enabled(), FakeAade() as aade, FakeStripeRefunds():
            _receipt(order)
            result = OrderService.refund_order(
                order, lines=[(_item(order, "13"), 1)]
            )

        assert result.value_returned == _eur("40.00")
        assert _rates(aade.credits[0]) == {CAT_13: Decimal("40.00")}

    def test_shipping_alone_can_be_refunded(self):
        order = _order(shipping="10.00")

        with FakeStripeRefunds():
            result = OrderService.refund_order(order, include_shipping=True)

        assert result.value_returned == _eur("10.00")

    def test_shipping_is_refunded_once(self):
        order = _order(shipping="10.00")

        with FakeStripeRefunds() as stripe_api:
            OrderService.refund_order(order, include_shipping=True)
            with pytest.raises(PaymentError, match="already refunded"):
                OrderService.refund_order(order, include_shipping=True)

        assert len(stripe_api.calls) == 1

    def test_an_order_with_no_shipping_has_none_to_refund(self):
        order = _order()

        with (
            FakeStripeRefunds() as stripe_api,
            pytest.raises(PaymentError, match="no shipping"),
        ):
            OrderService.refund_order(order, include_shipping=True)

        assert stripe_api.calls == []


class TestARetryUnderTheSameKey:
    def test_the_same_lines_reuse_the_pending_attempt_and_its_key(self):
        order = _order()
        item = _item(order, "13")

        with mydata_enabled(), FakeAade() as aade:
            _receipt(order)
            with FakeStripeRefunds(fail_with=stripe.APIConnectionError("drop")):
                first = OrderService.refund_order(order, lines=[(item, 1)])
            assert first.declined["outcome_unknown"] is True
            [pending] = order.refund_attempts.all()
            assert pending.status == RefundAttemptStatus.PENDING

            with FakeStripeRefunds() as stripe_api:
                again = OrderService.refund_order(order, lines=[(item, 1)])

        assert again.complete
        [attempt] = order.refund_attempts.all()
        assert attempt.pk == pending.pk
        assert stripe_api.keys == [f"refund-{pending.idempotency_key}"]
        assert attempt.lines.count() == 1
        [credit] = aade.credits
        assert _rates(credit) == {CAT_13: Decimal("40.00")}

    def test_other_lines_of_the_same_amount_are_not_a_retry(self):
        order = _order(lines=(("24", "40.00", 1), ("13", "40.00", 1)))

        with FakeStripeRefunds(fail_with=stripe.APIConnectionError("drop")):
            OrderService.refund_order(order, lines=[(_item(order, "24"), 1)])
        with FakeStripeRefunds() as stripe_api:
            OrderService.refund_order(order, lines=[(_item(order, "13"), 1)])

        assert len(order.refund_attempts.all()) == 2
        assert len(stripe_api.keys) == 1
        assert (
            stripe_api.keys[0]
            != f"refund-{order.refund_attempts.first().idempotency_key}"
        )

    def test_a_lines_attempt_is_not_the_retry_of_a_bare_amount(self):
        order = _order()

        with FakeStripeRefunds(fail_with=stripe.APIConnectionError("drop")):
            OrderService.refund_order(order, lines=[(_item(order, "13"), 1)])
        with FakeStripeRefunds() as stripe_api:
            OrderService.refund_order(order, amount=_eur("40.00"))

        assert order.refund_attempts.count() == 2
        assert len(stripe_api.calls) == 1


class TestNoLinePassesWhatWasSold:
    def test_a_line_cannot_be_refunded_twice(self):
        order = _order()
        item = _item(order, "13")

        with FakeStripeRefunds() as stripe_api:
            OrderService.refund_order(order, lines=[(item, 1)])
            with pytest.raises(PaymentError, match="already paid back"):
                OrderService.refund_order(order, lines=[(item, 1)])

        assert len(stripe_api.calls) == 1

    def test_more_units_than_were_sold_are_refused(self):
        order = _order(lines=(("13", "10.00", 2),))

        with (
            FakeStripeRefunds() as stripe_api,
            pytest.raises(PaymentError, match="2 were sold"),
        ):
            OrderService.refund_order(order, lines=[(_item(order, "13"), 3)])

        assert stripe_api.calls == []
        assert not RefundAttempt.objects.filter(order=order).exists()

    def test_units_of_a_declined_attempt_can_be_refunded_again(self):
        order = _order()
        item = _item(order, "13")

        with FakeStripeRefunds(fail_with=stripe.InvalidRequestError("no", "x")):
            OrderService.refund_order(order, lines=[(item, 1)])
        with FakeStripeRefunds():
            result = OrderService.refund_order(order, lines=[(item, 1)])

        assert result.complete

    def test_a_line_of_another_order_is_refused(self):
        order = _order()
        other = _order(payment_id="pi_other")

        with (
            FakeStripeRefunds(),
            pytest.raises(PaymentError, match="not on this order"),
        ):
            OrderService.refund_order(order, lines=[(_item(other, "13"), 1)])

    def test_an_amount_is_not_given_with_lines(self):
        order = _order()

        with FakeStripeRefunds(), pytest.raises(PaymentError):
            OrderService.refund_order(
                order,
                amount=_eur("1.00"),
                lines=[(_item(order, "13"), 1)],
            )

    def test_what_the_ledger_holds_still_caps_the_refund(self):
        order = _order()

        with FakeStripeRefunds() as stripe_api:
            OrderService.refund_order(order, amount=_eur("70.00"))
            with pytest.raises(PaymentError, match="cannot exceed"):
                OrderService.refund_order(
                    order, lines=[(_item(order, "13"), 1)]
                )

        assert len(stripe_api.calls) == 1


class TestTheCumulativeCapPerRate:
    def test_a_rate_the_receipt_no_longer_holds_is_never_credited(self):
        order = _order()

        with mydata_enabled(), FakeAade() as aade, FakeStripeRefunds():
            _receipt(order)
            # A goodwill 50.00 takes 30.00 of the 24% and 20.00 of the 13%.
            OrderService.refund_order(order, amount=_eur("50.00"))
            result = OrderService.refund_order(
                order, lines=[(_item(order, "13"), 1)]
            )

        # The money moved (the ledger allows it) but 13% holds 20.00 only:
        # the credit is not sent and staff are told on the order.
        assert result.complete
        assert len(aade.credits) == 1
        assert RetailCredit.objects.filter(order=order).count() == 1
        notes = [
            row.new_value["note"]
            for row in OrderHistory.objects.filter(
                order=order, change_type="NOTE"
            )
        ]
        assert any(
            n.startswith("No credit document could be issued") for n in notes
        )

    def test_credit_breakdown_refuses_a_rate_past_what_it_holds(self):
        receipt = [
            {
                "rate": "24.00",
                "subtotal": "48.39",
                "vat": "11.61",
                "gross": "60.00",
            },
            {
                "rate": "13.00",
                "subtotal": "35.40",
                "vat": "4.60",
                "gross": "40.00",
            },
        ]
        prior = [
            [
                {
                    "rate": "13.00",
                    "subtotal": "26.55",
                    "vat": "3.45",
                    "gross": "30.00",
                },
            ]
        ]

        with pytest.raises(CreditExceedsReceiptError):
            credit_breakdown(
                receipt,
                prior,
                Decimal("20.00"),
                {Decimal("13.00"): Decimal("20.00")},
            )

    def test_credit_breakdown_follows_the_allocation(self):
        receipt = [
            {
                "rate": "24.00",
                "subtotal": "48.39",
                "vat": "11.61",
                "gross": "60.00",
            },
            {
                "rate": "13.00",
                "subtotal": "35.40",
                "vat": "4.60",
                "gross": "40.00",
            },
        ]

        rows = credit_breakdown(
            receipt, [], Decimal("40.00"), {Decimal("13.00"): Decimal("40.00")}
        )

        assert rows == [
            {
                "rate": "13.00",
                "subtotal": "35.40",
                "vat": "4.60",
                "gross": "40.00",
            }
        ]


class TestGiftCardsAndPointsFollowTheRefundedLines:
    def test_the_provider_is_paid_first_and_the_rest_goes_back_to_the_card(
        self,
    ):
        # 100.00 of goods, 60.00 on a gift card and 40.00 by card.
        order = _order(gift="60.00")
        item24 = _item(order, "24")

        with mydata_enabled(), FakeAade() as aade, FakeStripeRefunds() as api:
            _receipt(order)
            result = OrderService.refund_order(order, lines=[(item24, 1)])

        # The 60.00 line: 40.00 through the provider, 20.00 back on the card.
        assert api.amounts == [4000]
        assert result.refunded == _eur("40.00")
        assert result.stored == _eur("20.00")
        attempts = list(order.refund_attempts.order_by("id"))
        assert [a.amount for a in attempts] == [_eur("40.00"), _eur("20.00")]
        # Every share's credit holds the rate of the line it pays for.
        assert len(aade.credits) == 2
        for credit in aade.credits:
            assert set(_rates(credit)) == {CAT_24}
        assert sum(c.net + c.vat for c in aade.credits) == Decimal("60.00")
        assert (
            order.payment_transactions.filter(
                kind=PaymentTransactionKind.REFUND
            ).count()
            == 1
        )
        assert PaymentLedger.stored_value(order).returned == Decimal("20.00")

    def test_a_line_split_over_two_shares_keeps_its_units_once(self):
        order = _order(lines=(("13", "50.00", 2),), gift="60.00")
        item = _item(order, "13")

        with FakeStripeRefunds():
            OrderService.refund_order(order, lines=[(item, 2)])

        rows = RefundAttemptLine.objects.filter(attempt__order=order)
        assert sum(row.quantity for row in rows) == 2
        assert sum(row.amount.amount for row in rows) == Decimal("100.00")


class TestSplitOverAttempts:
    def test_a_piece_is_cut_where_a_share_ends_and_keeps_its_units_first(self):
        piece = RefundPiece(
            kind=ChargeKind.GOODS,
            rate=Decimal("13.00"),
            amount=Decimal("30.00"),
            item_pk=1,
            quantity=3,
        )
        ship = RefundPiece(
            kind=ChargeKind.SHIPPING,
            rate=Decimal("24.00"),
            amount=Decimal("5.00"),
        )

        first, second = split_over_attempts(
            [piece, ship], [Decimal("20.00"), Decimal("15.00")]
        )

        assert [(p.amount, p.quantity) for p in first] == [
            (Decimal("20.00"), 3)
        ]
        assert [(p.kind, p.amount, p.quantity) for p in second] == [
            (ChargeKind.GOODS, Decimal("10.00"), 0),
            (ChargeKind.SHIPPING, Decimal("5.00"), 0),
        ]


class TestProviders:
    def test_stripe_is_called_with_the_line_amount_and_the_attempt_key(self):
        order = _order()

        with FakeStripeRefunds() as stripe_api:
            OrderService.refund_order(order, lines=[(_item(order, "13"), 1)])

        assert stripe_api.amounts == [4000]
        [attempt] = order.refund_attempts.all()
        assert stripe_api.keys == [f"refund-{attempt.idempotency_key}"]

    def test_viva_is_asked_to_cancel_the_line_amount(self):
        order = _order(provider="viva_wallet")
        item = _item(order, "13")

        with (
            mydata_enabled(),
            FakeAade() as aade,
            FakeVivaApi(cancel=(200, CANCEL_SUCCESS)) as viva,
        ):
            _receipt(order)
            result = OrderService.refund_order(order, lines=[(item, 1)])

        assert result.complete
        [delete] = viva.deletes
        assert viva.query(delete)["amount"] == ["4000"]
        [credit] = aade.credits
        assert _rates(credit) == {CAT_13: Decimal("40.00")}


class TestCashOnDelivery:
    def test_the_repayment_reference_is_needed_and_the_credit_follows_the_lines(
        self,
    ):
        order = _order(paid_online=False)
        item = _item(order, "13")

        with mydata_enabled(), FakeAade() as aade:
            _receipt(order)
            with pytest.raises(PaymentError, match="Say how the cash"):
                OrderService.refund_order(order, lines=[(item, 1)])
            result = OrderService.refund_order(
                order,
                lines=[(item, 1)],
                reason_code=RefundReason.RETURNED,
                repayment_reference="IBAN-TRANSFER-1",
            )

        assert result.complete
        [row] = order.payment_transactions.filter(
            kind=PaymentTransactionKind.REFUND
        )
        assert row.amount == _eur("40.00")
        [credit] = aade.credits
        assert _rates(credit) == {CAT_13: Decimal("40.00")}


class TestTheReasonIsPickedFromAList:
    def test_the_category_and_the_note_are_kept(self):
        order = _order()

        with FakeStripeRefunds():
            OrderService.refund_order(
                order,
                lines=[(_item(order, "13"), 1)],
                reason="Box was crushed",
                reason_code=RefundReason.DAMAGED,
            )

        [attempt] = order.refund_attempts.all()
        assert attempt.reason_code == RefundReason.DAMAGED
        assert attempt.reason == "Arrived damaged or defective: Box was crushed"
        [refund] = order.payment_transactions.filter(
            kind=PaymentTransactionKind.REFUND
        )
        assert refund.description == attempt.reason

    def test_a_refund_made_before_the_list_keeps_its_text(self):
        order = _order()

        with FakeStripeRefunds():
            OrderService.refund_order(
                order, amount=_eur("10.00"), reason="old free text"
            )

        [attempt] = order.refund_attempts.all()
        assert attempt.reason_code == ""
        assert attempt.reason == "old free text"


def _request(staff, data):
    request = RequestFactory().post(
        "/admin/order/order/", {"_form_submitted": "true", **data}
    )
    request.user = staff
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


@pytest.fixture
def staff():
    return UserAccountFactory(is_staff=True, is_superuser=True)


@pytest.fixture
def order_admin():
    return OrderAdmin(Order, AdminSite())


class TestTheReturnDialog:
    def test_lines_shipping_and_restock_in_one_step(self, order_admin, staff):
        order = _order(shipping="10.00")
        item = _item(order, "13")
        stock_before = item.product.stock
        request = _request(
            staff,
            {
                f"quantity_{item.pk}": "1",
                "include_shipping": "on",
                "restock": "on",
                "reason": RefundReason.RETURNED,
                "note": "came back Monday",
            },
        )

        with FakeStripeRefunds() as stripe_api:
            order_admin.refund_payment(request, object_id=order.pk)

        assert stripe_api.amounts == [5000]
        messages = [str(m) for m in get_messages(request)]
        assert f"Order {order.pk} refunded €50.00." in messages
        assert any("put back in stock" in m for m in messages)
        item.refresh_from_db()
        assert item.refunded_quantity == 1
        [attempt] = order.refund_attempts.all()
        assert attempt.reason_code == RefundReason.RETURNED
        assert "came back Monday" in attempt.reason
        item.product.refresh_from_db()
        assert item.product.stock == stock_before + 1

    def test_lines_and_a_goodwill_amount_together_are_refused(
        self, order_admin, staff
    ):
        order = _order()
        item = _item(order, "13")
        request = _request(
            staff,
            {
                f"quantity_{item.pk}": "1",
                "amount": "5.00",
                "reason": RefundReason.GOODWILL,
            },
        )

        with FakeStripeRefunds() as stripe_api:
            order_admin.refund_payment(request, object_id=order.pk)

        assert stripe_api.calls == []

    def test_the_reason_must_be_from_the_list(self, order_admin, staff):
        order = _order()
        item = _item(order, "13")
        request = _request(
            staff, {f"quantity_{item.pk}": "1", "reason": "because"}
        )

        with FakeStripeRefunds() as stripe_api:
            order_admin.refund_payment(request, object_id=order.pk)

        assert stripe_api.calls == []

    def test_a_goodwill_amount_alone_is_still_refunded(
        self, order_admin, staff
    ):
        order = _order()
        request = _request(
            staff, {"amount": "12.00", "reason": RefundReason.GOODWILL}
        )

        with FakeStripeRefunds() as stripe_api:
            order_admin.refund_payment(request, object_id=order.pk)

        assert stripe_api.amounts == [1200]
        assert not RefundAttemptLine.objects.exists()

    def test_the_dialog_offers_only_units_still_to_refund(self, staff):
        order = _order(lines=(("13", "10.00", 3),))
        item = _item(order, "13")
        with FakeStripeRefunds():
            OrderService.refund_order(order, lines=[(item, 2)])

        form = ReturnOrderForm(_request(staff, {}), order.pk)

        field = form.fields[f"quantity_{item.pk}"]
        assert field.max_value == 1
        assert "10.00" in str(field.help_text)

    def test_a_declined_refund_does_not_restock(self, order_admin, staff):
        order = _order()
        item = _item(order, "13")
        request = _request(
            staff,
            {
                f"quantity_{item.pk}": "1",
                "restock": "on",
                "reason": RefundReason.RETURNED,
            },
        )

        with FakeStripeRefunds(fail_with=stripe.InvalidRequestError("no", "x")):
            order_admin.refund_payment(request, object_id=order.pk)

        item.refresh_from_db()
        assert item.refunded_quantity == 0

    def test_cash_to_pay_back_asks_how(self, staff):
        order = _order(paid_online=False)

        form = ReturnOrderForm(_request(staff, {}), order.pk)

        assert "repayment_reference" in form.fields
