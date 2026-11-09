"""Refunding order lines: the amount from the frozen figures, and the myDATA
11.4 credit that carries exactly those lines' VAT rates.

Before this a refund was an amount, and its credit document spread it over
the receipt's rates in proportion, so returning one 13% item of a 24%/13%
order credited both rates. A refund by lines
(``OrderService.refund_order(lines=..., include_shipping=...)``) stores the
lines as its own credit allocation rows and the credit follows them. A bare amount is
a goodwill refund and stays in proportion.

Only the boundaries are faked: AADE (``FakeAade``), Stripe
(``FakeStripeRefunds``) and Viva (``FakeVivaApi``).
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
import stripe
from django.contrib.admin.sites import AdminSite
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.db.models import Sum
from django.test import RequestFactory
from djmoney.money import Money

from giftcard.factories import GiftCardFactory
from giftcard.models import GiftCardTransaction, GiftCardTransactionKind
from loyalty.enum import TransactionType
from loyalty.models.transaction import PointsTransaction
from order import credit_allocation
from order.admin import OrderAdmin, ReturnOrderForm
from order.credit_allocation import Piece, split_over_shares
from order.credit_documents import CreditExceedsReceiptError, credit_breakdown
from order.enum.document_type import OrderDocumentTypeEnum
from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.exceptions import PaymentError
from order.factories import OrderFactory, PaymentTransactionFactory
from order.factories.item import OrderItemFactory
from order.invoicing import generate_invoice
from order.models import (
    CreditAllocation,
    Invoice,
    Order,
    OrderItem,
    RetailCredit,
)
from order.models.invoice import MyDataStatus
from order.models.refund_attempt import (
    RefundAttempt,
    RefundAttemptStatus,
    RefundReason,
)
from order.payment_ledger import PaymentLedger
from order.refund_lines import held_values
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
            # The factory restocks a random tenth of its lines.
            is_refunded=False,
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


def _line_rows():
    """The allocation rows of refunds by lines (a goodwill refund has none
    for a line)."""
    return CreditAllocation.objects.filter(item__isnull=False)


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
    def test_the_named_request_is_resumed_under_its_own_key(self):
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
                again = OrderService.refund_order(
                    order, retry_request_id=pending.request_id
                )

        assert again.complete
        [attempt] = order.refund_attempts.all()
        assert attempt.pk == pending.pk
        assert stripe_api.keys == [f"refund-{pending.idempotency_key}"]
        assert attempt.allocations.count() == 1
        [credit] = aade.credits
        assert _rates(credit) == {CAT_13: Decimal("40.00")}

    def test_the_same_lines_while_a_refund_is_pending_are_not_paid_twice(self):
        order = _order()
        item = _item(order, "13")

        with FakeStripeRefunds(fail_with=stripe.APIConnectionError("drop")):
            OrderService.refund_order(order, lines=[(item, 1)])
        with (
            FakeStripeRefunds() as stripe_api,
            pytest.raises(PaymentError, match="already paid back"),
        ):
            OrderService.refund_order(order, lines=[(item, 1)])

        assert stripe_api.calls == []
        assert order.refund_attempts.count() == 1

    def test_a_retry_names_a_pending_request_and_nothing_else(self):
        order = _order()
        item = _item(order, "13")
        with FakeStripeRefunds(fail_with=stripe.APIConnectionError("drop")):
            OrderService.refund_order(order, lines=[(item, 1)])
        [pending] = order.refund_attempts.all()

        with FakeStripeRefunds() as stripe_api:
            with pytest.raises(PaymentError, match="no lines"):
                OrderService.refund_order(
                    order,
                    lines=[(item, 1)],
                    retry_request_id=pending.request_id,
                )
            with pytest.raises(PaymentError, match="no pending refund"):
                OrderService.refund_order(order, retry_request_id=uuid.uuid4())

        assert stripe_api.calls == []

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
    def test_a_rate_the_receipt_no_longer_holds_is_refused_before_money_moves(
        self,
    ):
        order = _order()

        with mydata_enabled(), FakeAade() as aade, FakeStripeRefunds() as api:
            _receipt(order)
            # A goodwill 50.00 takes 30.00 of the 24% and 20.00 of the 13%.
            OrderService.refund_order(order, amount=_eur("50.00"))
            with pytest.raises(PaymentError, match="goodwill refund"):
                OrderService.refund_order(
                    order, lines=[(_item(order, "13"), 1)]
                )
            # The goodwill route the message names still works.
            OrderService.refund_order(order, amount=_eur("40.00"))

        assert len(api.calls) == 2
        assert order.refund_attempts.count() == 2
        assert not _line_rows().exists()
        assert len(aade.credits) == 2

    def test_pending_line_allocations_count_against_the_receipt(self):
        order = _order(lines=(("24", "60.00", 1), ("13", "20.00", 3)))
        item = _item(order, "13")

        with mydata_enabled(), FakeAade() as aade, FakeStripeRefunds() as api:
            _receipt(order)
            # 20.00 goodwill: 10.00 of the 24% and 10.00 of the 13%.
            OrderService.refund_order(order, amount=_eur("20.00"))
            with FakeStripeRefunds(fail_with=stripe.APIConnectionError("drop")):
                OrderService.refund_order(order, lines=[(item, 1)])
            # The 13% holds 60.00; 10.00 credited and 20.00 pending leave
            # 30.00, which two more units (40.00) do not fit. They would
            # fit were the claim in flight released: the refund waits.
            with pytest.raises(PaymentError, match="waiting for"):
                OrderService.refund_order(order, lines=[(item, 2)])
        assert len(api.calls) == 1
        assert len(aade.credits) == 1

    def test_before_the_receipt_the_order_lines_are_the_headroom(self):
        order = _order()

        with FakeStripeRefunds() as api:
            # A goodwill 50.00 takes 30.00 of the 24% and 20.00 of the 13%.
            OrderService.refund_order(order, amount=_eur("50.00"))
            # The 13% item (40.00) no longer fits what its rate holds, with
            # no receipt either: the order's own VAT lines are the room.
            with pytest.raises(PaymentError, match="holds only"):
                OrderService.refund_order(
                    order, lines=[(_item(order, "13"), 1)]
                )
            # The goodwill route the message names still works.
            result = OrderService.refund_order(order, amount=_eur("40.00"))

        assert result.complete
        assert len(api.calls) == 2
        assert not _line_rows().exists()

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

    def test_a_line_split_over_two_shares_carries_its_value_on_both(self):
        order = _order(lines=(("13", "50.00", 2),), gift="60.00")
        item = _item(order, "13")

        with FakeStripeRefunds():
            OrderService.refund_order(order, lines=[(item, 2)])

        rows = _line_rows().filter(order=order)
        assert sorted(row.amount.amount for row in rows) == [
            Decimal("40.00"),
            Decimal("60.00"),
        ]
        assert {row.quantity for row in rows} == {2}


class TestSplitOverShares:
    def test_a_piece_is_cut_where_a_share_ends_and_both_parts_name_the_request(
        self,
    ):
        piece = Piece(
            kind=ChargeKind.GOODS,
            rate=Decimal("13.00"),
            amount=Decimal("30.00"),
            item_pk=1,
            quantity=3,
        )
        ship = Piece(
            kind=ChargeKind.SHIPPING,
            rate=Decimal("24.00"),
            amount=Decimal("5.00"),
        )

        first, second = split_over_shares(
            [piece, ship], [Decimal("20.00"), Decimal("15.00")]
        )

        assert [(p.amount, p.quantity) for p in first] == [
            (Decimal("20.00"), 3)
        ]
        assert [(p.kind, p.amount, p.quantity) for p in second] == [
            (ChargeKind.GOODS, Decimal("10.00"), 3),
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
        assert not _line_rows().exists()

    def test_the_dialog_offers_only_units_still_to_refund(self, staff):
        order = _order(lines=(("13", "10.00", 3),))
        item = _item(order, "13")
        with FakeStripeRefunds():
            OrderService.refund_order(order, lines=[(item, 2)])

        form = ReturnOrderForm(_request(staff, {}), order.pk)

        field = form.fields[f"quantity_{item.pk}"]
        assert field.max_value == 1
        assert "€10.00 left to refund" in str(field.help_text)

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


def _two_charges(order, first: str, second: str) -> None:
    """The order paid in two payments instead of one."""
    order.payment_transactions.all().delete()
    for reference, amount in (("pi_first", first), ("pi_second", second)):
        PaymentTransactionFactory(
            order=order, amount=_eur(amount), provider_reference=reference
        )


class _SecondCallRefused(FakeStripeRefunds):
    """Stripe refuses the second refund call, and every one after it, until
    ``refusing`` is switched off (one Stripe, so refund ids never repeat)."""

    refusing = True

    def _create(self, **kwargs):
        if self.refusing and len(self.calls) >= 1:
            self.calls.append(kwargs)
            raise stripe.InvalidRequestError("no", "x")
        return super()._create(**kwargs)


class TestAValueIsHeldNotACountOfUnits:
    def test_a_line_split_over_two_charges_keeps_the_declined_part_on_offer(
        self, order_admin, staff
    ):
        order = _order(lines=(("13", "100.00", 1),))
        _two_charges(order, "60.00", "40.00")
        item = _item(order, "13")

        with _SecondCallRefused() as api:
            result = OrderService.refund_order(order, lines=[(item, 1)])
            assert not result.complete
            assert result.refunded == _eur("60.00")
            # 60.00 came back, so the line is still offered, for what is
            # left.
            form = ReturnOrderForm(_request(staff, {}), order.pk)
            field = form.fields[f"quantity_{item.pk}"]
            assert field.max_value == 1
            assert "€40.00 left to refund" in str(field.help_text)
            # Asking for it again claims what is left, not the whole line.
            api.refusing = False
            again = OrderService.refund_order(order, lines=[(item, 1)])
        assert again.complete
        assert again.refunded == _eur("40.00")
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.REFUNDED

    def test_the_dialog_does_not_restock_a_refund_that_was_not_made_in_full(
        self, order_admin, staff
    ):
        order = _order(lines=(("13", "100.00", 1),))
        _two_charges(order, "60.00", "40.00")
        item = _item(order, "13")
        request = _request(
            staff,
            {
                f"quantity_{item.pk}": "1",
                "restock": "on",
                "reason": RefundReason.RETURNED,
            },
        )

        with _SecondCallRefused():
            order_admin.refund_payment(request, object_id=order.pk)

        item.refresh_from_db()
        assert item.refunded_quantity == 0

    def test_a_later_request_of_the_same_units_pays_its_own_quote(self):
        # Two units at 50.00; the charges are 30.00 and 70.00.
        order = _order(lines=(("13", "50.00", 2),))
        _two_charges(order, "30.00", "70.00")
        item = _item(order, "13")

        with _SecondCallRefused() as api:
            first = OrderService.refund_order(order, lines=[(item, 1)])
            assert not first.complete
            assert first.refunded == _eur("30.00")
            api.refusing = False
            # Same units again: quoted at what is left of the first unit,
            # then at the whole second unit. Never the declined share's old
            # claim, never a request that reports complete for a quote it
            # did not pay.
            paid = [
                OrderService.refund_order(order, lines=[(item, 1)])
                for _ in range(2)
            ]

        assert [r.refunded for r in paid] == [_eur("20.00"), _eur("50.00")]
        assert all(r.complete for r in paid)
        assert api.amounts[-2:] == [2000, 5000]
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.REFUNDED
        # Nothing is held twice.
        rows = CreditAllocation.objects.counting().filter(
            order=order, item__isnull=False
        )
        assert sum(r.amount.amount for r in rows) == Decimal("100.00")

    def test_card_pending_and_gift_card_declined_retries_the_card_only(self):
        order = _order(lines=(("13", "100.00", 1),), gift="60.00")
        item = _item(order, "13")

        with FakeStripeRefunds(fail_with=stripe.APIConnectionError("drop")):
            first = OrderService.refund_order(order, lines=[(item, 1)])
        assert first.declined["outcome_unknown"] is True
        card, gift = order.refund_attempts.order_by("id")
        assert card.status == RefundAttemptStatus.PENDING
        assert gift.status == RefundAttemptStatus.DECLINED

        with FakeStripeRefunds() as api:
            again = OrderService.refund_order(
                order, retry_request_id=card.request_id
            )
            # The gift-card part freed its value, so a new request of the
            # line covers what is left.
            rest = OrderService.refund_order(order, lines=[(item, 1)])

        assert again.complete
        assert api.keys == [f"refund-{card.idempotency_key}"]
        assert rest.stored == _eur("60.00")
        assert PaymentLedger.stored_value(order).returned == Decimal("60.00")

    def test_shipping_split_across_shares_keeps_what_is_left_on_offer(self):
        order = _order(shipping="10.00")
        _two_charges(order, "5.00", "105.00")

        from order.refund_lines import held_values, plan_pieces

        with _SecondCallRefused() as api:
            OrderService.refund_order(order, include_shipping=True)
            api.refusing = False
            self._stripe = api

        # 5.00 of the 6.00 shipping at 24% went back; the rest is still owed.
        assert held_values(order).shipping == {
            Decimal("24.00"): Decimal("5.00")
        }
        left = plan_pieces(order, {}, include_shipping=True)
        assert [(p.rate, p.amount) for p in left] == [
            (Decimal("24.00"), Decimal("1.00")),
            (Decimal("13.00"), Decimal("4.00")),
        ]
        with self._stripe:
            result = OrderService.refund_order(order, include_shipping=True)
        assert result.complete
        assert result.refunded == _eur("5.00")
        assert held_values(order).shipping_total() == Decimal("10.00")
        with pytest.raises(PaymentError, match="already refunded"):
            plan_pieces(order, {}, include_shipping=True)


class TestRetryingThePendingRefundFromTheDialog:
    def _pending(self):
        order = _order()
        item = _item(order, "13")
        with FakeStripeRefunds(fail_with=stripe.APIConnectionError("drop")):
            OrderService.refund_order(order, lines=[(item, 1)])
        [attempt] = order.refund_attempts.all()
        return order, item, attempt

    def test_the_dialog_offers_the_retry_and_no_longer_the_held_line(
        self, staff
    ):
        order, item, _attempt = self._pending()

        form = ReturnOrderForm(_request(staff, {}), order.pk)

        assert "retry_pending" in form.fields
        assert "€40.00" in str(form.fields["retry_pending"].help_text)
        assert f"quantity_{item.pk}" not in form.fields

    def test_the_retry_resumes_the_same_attempt_under_the_same_key(
        self, order_admin, staff
    ):
        order, _, attempt = self._pending()
        request = _request(
            staff,
            {"retry_pending": "on", "reason": RefundReason.RETURNED},
        )

        with FakeStripeRefunds() as api:
            order_admin.refund_payment(request, object_id=order.pk)

        assert api.keys == [f"refund-{attempt.idempotency_key}"]
        assert order.refund_attempts.count() == 1
        attempt.refresh_from_db()
        assert attempt.status == RefundAttemptStatus.SETTLED
        assert f"Order {order.pk} refunded €40.00." in [
            str(m) for m in get_messages(request)
        ]

    def test_the_retry_is_not_offered_when_nothing_is_pending(self, staff):
        order = _order()

        form = ReturnOrderForm(_request(staff, {}), order.pk)

        assert "retry_pending" not in form.fields

    def test_the_retry_is_not_mixed_with_a_goodwill_amount(
        self, order_admin, staff
    ):
        order, _, _attempt = self._pending()
        request = _request(
            staff,
            {
                "retry_pending": "on",
                "amount": "5.00",
                "reason": RefundReason.RETURNED,
            },
        )

        with FakeStripeRefunds() as api:
            order_admin.refund_payment(request, object_id=order.pk)

        assert api.calls == []
        assert order.refund_attempts.count() == 1


class TestTheReceiptHeadroomSimulatesTheCreditsToCome:
    def test_a_refund_before_the_receipt_is_netted_at_its_own_rate(self):
        order = _order()

        with mydata_enabled(), FakeAade() as aade, FakeStripeRefunds():
            OrderService.refund_order(order, lines=[(_item(order, "13"), 1)])
            invoice = _receipt(order)
            # The 13% item went back before the receipt: it is not on it,
            # and the 24% row is whole (not cut in proportion).
            assert [Decimal(r["rate"]) for r in invoice.vat_breakdown] == [
                Decimal(24)
            ]
            assert Decimal(invoice.vat_breakdown[0]["gross"]) == Decimal(
                "60.00"
            )
            # A legitimate return after it fits.
            result = OrderService.refund_order(
                order, lines=[(_item(order, "24"), 1)]
            )

        assert result.complete
        [credit] = aade.credits
        assert _rates(credit) == {CAT_24: Decimal("60.00")}

    def test_a_bare_refund_before_the_receipt_still_nets_in_proportion(self):
        order = _order()

        with mydata_enabled(), FakeAade(), FakeStripeRefunds():
            OrderService.refund_order(order, amount=_eur("50.00"))
            invoice = _receipt(order)

        assert {
            Decimal(r["rate"]): Decimal(r["gross"])
            for r in invoice.vat_breakdown
        } == {Decimal(24): Decimal("30.00"), Decimal(13): Decimal("20.00")}

    def test_a_goodwill_refund_whose_credit_is_not_issued_yet_counts(self):
        order = _order()

        with mydata_enabled(), FakeAade(), FakeStripeRefunds() as api:
            # A receipt row exists, not registered with AADE: no credit is
            # issued for what follows, and the check applies all the same.
            generate_invoice(order)
            OrderService.refund_order(order, amount=_eur("50.00"))
            with pytest.raises(PaymentError, match="holds only"):
                OrderService.refund_order(
                    order, lines=[(_item(order, "13"), 1)]
                )

        assert len(api.calls) == 1

    def test_credit_breakdown_never_spills_a_capped_rate_onto_another(self):
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
                }
            ]
        ]
        lines = {
            Decimal("24.00"): Decimal("60.00"),
            Decimal("13.00"): Decimal("40.00"),
        }

        with pytest.raises(CreditExceedsReceiptError):
            credit_breakdown(receipt, prior, Decimal("100.00"), lines)

    def test_a_stored_share_that_returned_less_takes_its_lines_in_proportion(
        self,
    ):
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
        lines = {
            Decimal("24.00"): Decimal("60.00"),
            Decimal("13.00"): Decimal("40.00"),
        }

        rows = credit_breakdown(receipt, [], Decimal("50.00"), lines)

        assert {r["rate"]: r["gross"] for r in rows} == {
            "24.00": "30.00",
            "13.00": "20.00",
        }


class TestLinesWorthNothing:
    def _free_gift_order(self):
        return _order(lines=(("24", "60.00", 1), ("13", "0.00", 1)))

    def test_a_free_gift_is_offered_and_returns_to_the_shelf_only(
        self, order_admin, staff
    ):
        order = self._free_gift_order()
        gift = _item(order, "13")
        request = _request(
            staff,
            {
                f"quantity_{gift.pk}": "1",
                "restock": "on",
                "reason": RefundReason.RETURNED,
            },
        )

        with FakeStripeRefunds() as api:
            order_admin.refund_payment(request, object_id=order.pk)

        assert api.calls == []
        gift.refresh_from_db()
        assert gift.refunded_quantity == 1
        assert not _line_rows().exists()

    def test_without_the_restock_there_is_nothing_to_do(
        self, order_admin, staff
    ):
        order = self._free_gift_order()
        gift = _item(order, "13")
        request = _request(
            staff,
            {f"quantity_{gift.pk}": "1", "reason": RefundReason.RETURNED},
        )

        with FakeStripeRefunds() as api:
            order_admin.refund_payment(request, object_id=order.pk)

        assert api.calls == []
        gift.refresh_from_db()
        assert gift.refunded_quantity == 0

    def test_the_free_gift_next_to_a_paid_line_adds_no_money(self):
        order = self._free_gift_order()

        with FakeStripeRefunds():
            result = OrderService.refund_order(
                order,
                lines=[(_item(order, "13"), 1), (_item(order, "24"), 1)],
            )

        assert result.value_returned == _eur("60.00")


class TestAStoredShareThatReturnedLess:
    def test_its_lines_are_scaled_to_what_it_returned(self):
        order = _order(lines=(("13", "100.00", 1),), gift="60.00")
        item = _item(order, "13")
        with FakeStripeRefunds():
            OrderService.refund_order(order, lines=[(item, 1)])
        stored = order.refund_attempts.get(kind="STORED_VALUE")
        assert stored.allocations.get().amount == _eur("60.00")

        credit_allocation.scale(Decimal("50.00"), refund_attempt=stored)

        assert stored.allocations.get().amount == _eur("50.00")
        # The unreturned 10.00 is refundable again, not held.
        assert held_values(order).goods[item.pk] == Decimal("90.00")

    def test_a_share_that_returned_nothing_holds_nothing(self):
        order = _order(lines=(("13", "100.00", 1),), gift="60.00")
        with FakeStripeRefunds():
            OrderService.refund_order(order, lines=[(_item(order, "13"), 1)])
        stored = order.refund_attempts.get(kind="STORED_VALUE")

        credit_allocation.scale(Decimal(0), refund_attempt=stored)

        assert not stored.allocations.filter(amount__gt=0).exists()


class TestTheLinesWriteTheirOwnAllocation:
    """What the rework to credit allocations (PR 530) fixed for lines: the
    room is stored at the claim, so no later credit and no clock changes
    what a refund holds."""

    def test_a_declined_attempt_frees_its_units_and_a_pending_one_never_lets_go(
        self,
    ):
        order = _order(lines=(("13", "20.00", 2),))
        item = _item(order, "13")

        with FakeStripeRefunds(fail_with=stripe.APIConnectionError("drop")):
            OrderService.refund_order(order, lines=[(item, 2)])
        [pending] = order.refund_attempts.all()
        # Two hours later the claim still holds its lines: there is no timer.
        RefundAttempt.objects.filter(pk=pending.pk).update(
            created_at=pending.created_at - timedelta(hours=2)
        )
        with (
            FakeStripeRefunds(),
            pytest.raises(PaymentError, match="paid back"),
        ):
            OrderService.refund_order(order, lines=[(item, 1)])
        assert held_values(order).goods == {item.pk: Decimal("40.00")}

        # Declined, its units are on offer again at once.
        RefundAttempt.objects.filter(pk=pending.pk).update(
            status=RefundAttemptStatus.DECLINED
        )
        assert held_values(order).goods == {}
        with FakeStripeRefunds():
            result = OrderService.refund_order(order, lines=[(item, 2)])
        assert result.complete

    def test_lines_credit_only_their_own_rates_after_an_earlier_goodwill_refund(
        self,
    ):
        order = _order(lines=(("24", "60.00", 1), ("13", "20.00", 2)))
        item = _item(order, "13")

        with mydata_enabled(), FakeAade() as aade, FakeStripeRefunds() as api:
            _receipt(order)
            # A goodwill 20.00 is claimed first and its answer is lost: it
            # holds 12.00 of the 24% and 8.00 of the 13% on the receipt.
            api.fail_with = stripe.APIConnectionError("x")
            OrderService.refund_order(order, amount=_eur("20.00"))
            api.fail_with = None
            # The lines settle first, so their credit is issued first.
            OrderService.refund_order(order, lines=[(item, 1)])
            # The goodwill claim settles after; its credit is the split it
            # was allocated, not what the lines left behind.
            OrderService.refund_order(order, amount=_eur("20.00"))

        goodwill, lines = sorted(aade.credits, key=lambda c: len(_rates(c)))[
            ::-1
        ]
        assert _rates(lines) == {CAT_13: Decimal("20.00")}
        assert _rates(goodwill) == {
            CAT_24: Decimal("12.00"),
            CAT_13: Decimal("8.00"),
        }

    def test_a_line_paid_partly_with_points_cannot_be_refunded_again(self):
        # A 100.00 line, 10.00 of it paid with points (a discount: the
        # receipt holds 90.00), 40.00 with a gift card, 50.00 by card.
        user = UserAccountFactory(num_addresses=0)
        order = _order(lines=(("13", "100.00", 1),), gift="40.00", user=user)
        item = _item(order, "13")
        OrderItem.objects.filter(pk=item.pk).update(
            discount_allocation=_eur("10.00")
        )
        Order.objects.filter(pk=order.pk).update(
            loyalty_discount=_eur("10.00"), amount_due=_eur("50.00")
        )
        order.payment_transactions.update(amount=_eur("50.00"))
        PointsTransaction.objects.create(
            user=user, points=10_000, transaction_type=TransactionType.EARN
        )
        PointsTransaction.objects.create(
            user=user,
            points=-1000,
            transaction_type=TransactionType.REDEEM,
            reference_order=order,
        )
        order.refresh_from_db()

        with FakeStripeRefunds():
            result = OrderService.refund_order(order, lines=[(item, 1)])
            assert result.complete
            # 50.00 by card, then 40.00 split over the gift card (32.00) and
            # the points (8.00): only the first 82.00 is room on the receipt.
            room = CreditAllocation.objects.counting().filter(order=order)
            assert room.aggregate(total=Sum("amount"))["total"] == Decimal(
                "82.00"
            )
            # The line has had its whole 90.00 back, points included.
            assert held_values(order).goods == {item.pk: Decimal("90.00")}
            with pytest.raises(PaymentError, match="paid back"):
                OrderService.refund_order(order, lines=[(item, 1)])

    def test_a_goodwill_amount_waits_for_the_claim_that_holds_its_room(self):
        order = _order(lines=(("24", "60.00", 1), ("13", "40.00", 1)))
        real = credit_allocation.allocate_pro_rata

        def short_unless_released(order, amount, **kwargs):
            if kwargs.get("settled_only") or kwargs.get("released"):
                return real(order, amount, **kwargs)
            return {}

        with FakeStripeRefunds() as api:
            api.fail_with = stripe.APIConnectionError("x")
            OrderService.refund_order(order, amount=_eur("20.00"))
            api.fail_with = None
            with (
                patch.object(
                    credit_allocation,
                    "allocate_pro_rata",
                    short_unless_released,
                ),
                pytest.raises(PaymentError, match="waiting for"),
            ):
                OrderService.refund_order(order, amount=_eur("10.00"))
            # Short with every claim released too: a final refusal.
            with (
                patch.object(
                    credit_allocation,
                    "allocate_pro_rata",
                    lambda *_a, **_k: {},
                ),
                pytest.raises(PaymentError, match="holds only"),
            ):
                OrderService.refund_order(order, amount=_eur("10.00"))

    def test_the_dialog_reads_a_lines_retry_as_the_locked_path_does(self):
        order = _order()
        item = _item(order, "13")
        with FakeStripeRefunds(fail_with=stripe.APIConnectionError("drop")):
            OrderService.refund_order(order, lines=[(item, 1)])
        [pending] = order.refund_attempts.all()

        # The named retry resumes the lines refund; a bare amount of the
        # same value never does (its shares carry a request id).
        assert OrderService.resumes_a_refund(
            order, None, retry_request_id=pending.request_id
        )
        assert not OrderService.resumes_a_refund(order, _eur("40.00"))
        assert not OrderService.resumes_a_refund(
            order, None, retry_request_id=uuid.uuid4()
        )
