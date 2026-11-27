"""Paying for an order clears that order's lines, not the whole cart.

An online order deliberately leaves the cart standing until the payment
lands — ``handle_order_created`` skips the clear while
``awaits_online_payment``, so a shopper who presses Back still has a
basket — and a hosted payment page can stay open for hours. Anything the
shopper adds during that window belongs to them, not to the order being
paid for.

The cart is a per-user singleton, so the order's cart and the shopper's
current cart are the same row. Only the LINES tell them apart.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from djmoney.money import Money

from cart.factories.cart import CartFactory
from cart.factories.item import CartItemFactory
from cart.models import Cart
from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models.item import OrderItem
from order.payments import stripe_settlement, viva_settlement
from order.payments.cart_clear import clear_cart_for_order
from order.services import OrderService
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory
from tests.utils.viva import verified_payment
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


def _ordered(order, product, quantity):
    return OrderItem.objects.create(
        order=order,
        product=product,
        quantity=quantity,
        price=Money(Decimal("10.00"), "EUR"),
        sort_order=1,
    )


def test_an_item_added_while_the_payment_was_open_survives():
    user = UserAccountFactory(num_addresses=0)
    ordered_product = ProductFactory(num_images=0, num_reviews=0)
    added_later = ProductFactory(num_images=0, num_reviews=0)

    cart = CartFactory(user=user, num_cart_items=0)
    CartItemFactory(cart=cart, product=ordered_product, quantity=2)

    order = OrderFactory(user=user, num_order_items=0)
    _ordered(order, ordered_product, 2)

    # The shopper goes back to the store while the hosted page is open.
    CartItemFactory(cart=cart, product=added_later, quantity=1)

    clear_cart_for_order(order)

    cart.refresh_from_db()
    remaining = list(cart.items.values_list("product_id", flat=True))
    assert remaining == [added_later.id], (
        "the shopper's newly added item was destroyed with the order's"
    )


def test_a_cart_holding_only_the_order_is_removed():
    user = UserAccountFactory(num_addresses=0)
    product = ProductFactory(num_images=0, num_reviews=0)

    cart = CartFactory(user=user, num_cart_items=0)
    CartItemFactory(cart=cart, product=product, quantity=3)

    order = OrderFactory(user=user, num_order_items=0)
    _ordered(order, product, 3)

    clear_cart_for_order(order)

    assert not Cart.objects.filter(pk=cart.pk).exists()


def test_extra_quantity_of_an_ordered_product_is_kept():
    """Two of X were ordered; the shopper then added a third."""
    user = UserAccountFactory(num_addresses=0)
    product = ProductFactory(num_images=0, num_reviews=0)

    cart = CartFactory(user=user, num_cart_items=0)
    CartItemFactory(cart=cart, product=product, quantity=3)

    order = OrderFactory(user=user, num_order_items=0)
    _ordered(order, product, 2)

    clear_cart_for_order(order)

    cart.refresh_from_db()
    assert cart.items.get(product=product).quantity == 1


def test_a_line_the_cart_never_had_removes_nothing():
    """Promotion gifts are injected onto the order at creation and were
    never cart items."""
    user = UserAccountFactory(num_addresses=0)
    ordered_product = ProductFactory(num_images=0, num_reviews=0)
    gift = ProductFactory(num_images=0, num_reviews=0)

    cart = CartFactory(user=user, num_cart_items=0)
    CartItemFactory(cart=cart, product=ordered_product, quantity=1)

    order = OrderFactory(user=user, num_order_items=0)
    _ordered(order, ordered_product, 1)
    _ordered(order, gift, 1)

    clear_cart_for_order(order)

    assert not Cart.objects.filter(pk=cart.pk).exists()


def test_guest_cart_is_scoped_by_the_snapshot_uuid():
    product = ProductFactory(num_images=0, num_reviews=0)
    # Created directly: CartFactory does get_or_create on ``user``, so a
    # second CartFactory(user=None) hands back the same guest row.
    guest_cart = Cart.objects.create(user=None)
    CartItemFactory(cart=guest_cart, product=product, quantity=1)

    other_cart = Cart.objects.create(user=None)
    CartItemFactory(cart=other_cart, product=product, quantity=1)

    order = OrderFactory(user=None, num_order_items=0)
    order.metadata = {"cart_snapshot": {"cart_uuid": str(guest_cart.uuid)}}
    order.save(update_fields=["metadata"])
    _ordered(order, product, 1)

    clear_cart_for_order(order)

    assert not Cart.objects.filter(pk=guest_cart.pk).exists()
    assert Cart.objects.filter(pk=other_cart.pk).exists()


def test_a_promotion_gift_of_an_ordered_product_keeps_the_shoppers_extra_unit():
    """Buy-2-get-1-free injects a €0 line for the same product; if the
    shopper added a 3rd unit while paying, only the 2 units from
    ``checkout_fingerprint.lines`` are removed."""
    user = UserAccountFactory(num_addresses=0)
    product = ProductFactory(num_images=0, num_reviews=0)

    cart = CartFactory(user=user, num_cart_items=0)
    CartItemFactory(cart=cart, product=product, quantity=3)

    order = OrderFactory(
        user=user,
        pay_way=PayWayFactory.create_online_payment("stripe"),
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
        num_order_items=0,
        metadata={
            "cart_snapshot": {"cart_uuid": str(cart.uuid)},
            "checkout_fingerprint": {"lines": {str(product.pk): 2}},
        },
    )
    _ordered(order, product, 2)
    _ordered(order, product, 1)  # injected free gift

    clear_cart_for_order(order)

    cart.refresh_from_db()
    assert cart.items.get(product=product).quantity == 1


class TestSettlementClearsCartAtomicallyOnce:
    """Every online settlement path removes the order's lines inside its own
    transaction (before any ``on_commit`` callback runs) and never removes a
    second unit when a follow-up or redelivered event arrives (#839)."""

    @staticmethod
    def _order_and_cart(*, provider_code: str, extra_qty: int = 1):
        user = UserAccountFactory(num_addresses=0)
        product = ProductFactory(num_images=0, num_reviews=0)
        cart = CartFactory(user=user, num_cart_items=0)
        CartItemFactory(cart=cart, product=product, quantity=1 + extra_qty)
        pay_way = PayWayFactory.create_online_payment(provider_code)
        order = OrderFactory(
            user=user,
            pay_way=pay_way,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            num_order_items=0,
            metadata={"cart_snapshot": {"cart_uuid": str(cart.uuid)}},
        )
        _ordered(order, product, 1)
        return order, cart, product

    def test_stripe_payment_intent_clears_inside_transaction_and_once(self):
        order, cart, product = self._order_and_cart(provider_code="stripe")
        order.payment_id = "pi_clear_once_1"
        order.save(update_fields=["payment_id"])
        total = order.calculate_order_total_amount()

        OrderService.handle_payment_succeeded("pi_clear_once_1", captured=total)

        # Even inside django_db's un-committed test transaction (no on_commit
        # callbacks fired), the order's unit is already gone while the
        # shopper's extra unit survives.
        assert cart.items.get(product=product).quantity == 1

        # Redelivered payment_intent.succeeded and a follow-up Checkout Session
        # event must not remove the shopper's remaining unit.
        OrderService.handle_payment_succeeded("pi_clear_once_1", captured=total)
        stripe_settlement.settle_checkout_session(
            order.pk,
            session_id="cs_after_pi_1",
            payment_intent_id="pi_clear_once_1",
            captured=total,
        )
        assert cart.items.get(product=product).quantity == 1

    def test_stripe_checkout_session_clears_inside_transaction_and_once(self):
        order, cart, product = self._order_and_cart(provider_code="stripe")
        total = order.calculate_order_total_amount()

        stripe_settlement.settle_checkout_session(
            order.pk,
            session_id="cs_clear_once_1",
            payment_intent_id="pi_from_cs_1",
            captured=total,
        )

        assert cart.items.get(product=product).quantity == 1

        # Follow-up payment_intent.succeeded for the same intent leaves the
        # remaining unit untouched.
        OrderService.handle_payment_succeeded("pi_from_cs_1", captured=total)
        assert cart.items.get(product=product).quantity == 1

    def test_viva_settlement_clears_inside_transaction_and_once(
        self, monkeypatch
    ):
        order, cart, product = self._order_and_cart(provider_code="viva_wallet")
        order.metadata["viva_order_codes"] = ["OC_CLEAR_1"]
        order.save(update_fields=["metadata"])
        monkeypatch.setattr(
            viva_settlement,
            "verify_transaction",
            lambda _tid: (
                PaymentStatus.COMPLETED,
                verified_payment(order, "OC_CLEAR_1"),
            ),
        )

        viva_settlement.apply_event(
            order,
            event_type_id=viva_settlement.VIVA_TRANSACTION_PAYMENT_CREATED,
            event_data={"StatusId": "F"},
            transaction_id="viva_txn_clear_1",
            order_code="OC_CLEAR_1",
            status_id="F",
            handle=viva_settlement.handle_payment_created,
            read=viva_settlement.read_payment_created,
        )

        assert cart.items.get(product=product).quantity == 1

        # A redelivery of the same transaction does not remove the extra unit.
        viva_settlement.apply_event(
            order,
            event_type_id=viva_settlement.VIVA_TRANSACTION_PAYMENT_CREATED,
            event_data={"StatusId": "F"},
            transaction_id="viva_txn_clear_1",
            order_code="OC_CLEAR_1",
            status_id="F",
            handle=viva_settlement.handle_payment_created,
            read=viva_settlement.read_payment_created,
        )
        assert cart.items.get(product=product).quantity == 1

        # A second transaction for the same settled order does not remove the
        # extra unit either.
        viva_settlement.apply_event(
            order,
            event_type_id=viva_settlement.VIVA_TRANSACTION_PAYMENT_CREATED,
            event_data={"StatusId": "F"},
            transaction_id="viva_txn_clear_2",
            order_code="OC_CLEAR_1",
            status_id="F",
            handle=viva_settlement.handle_payment_created,
            read=viva_settlement.read_payment_created,
        )
        assert cart.items.get(product=product).quantity == 1

    def test_payment_after_cancel_keeps_the_cart(self):
        order, cart, product = self._order_and_cart(
            provider_code="stripe", extra_qty=0
        )
        order.payment_id = "pi_after_cancel_cart"
        order.status = OrderStatus.CANCELED
        order.payment_status = PaymentStatus.CANCELED
        order.save(update_fields=["payment_id", "status", "payment_status"])

        OrderService.handle_payment_succeeded(
            "pi_after_cancel_cart",
            captured=order.calculate_order_total_amount(),
        )

        assert cart.items.get(product=product).quantity == 1

    def test_amount_mismatch_keeps_the_cart(self):
        order, cart, product = self._order_and_cart(
            provider_code="stripe", extra_qty=0
        )
        order.payment_id = "pi_mismatch_cart"
        order.save(update_fields=["payment_id"])

        OrderService.handle_payment_succeeded(
            "pi_mismatch_cart",
            captured=Money(Decimal("1.00"), "EUR"),
        )

        assert cart.items.get(product=product).quantity == 1
