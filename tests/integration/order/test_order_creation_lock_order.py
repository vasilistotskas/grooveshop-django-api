"""Product rows are locked in ascending pk order, whatever the cart's lines.

Order creation locked ``Product`` rows in each cart's own line order: the
cart-item query (``FOR UPDATE`` with no ``OF``) locked the joined products
newest line first, and the stock step then locked them again in the order
of a dict built from those lines. Two checkouts holding the same products
in different line orders took them in opposite orders, which is a
deadlock; PostgreSQL resolves it by aborting one shopper's checkout.

A cycle needs two transactions taking shared rows in different orders, so
the rule is one global order, the primary key. These tests read it off the
SQL a checkout issues, which is the only place the lock order exists.

Taking the product locks BEFORE the first ``OrderItem`` insert matters as
much as their order: PostgreSQL takes ``FOR KEY SHARE`` on the referenced
product for every such insert, and ``FOR KEY SHARE`` conflicts with ``FOR
UPDATE``. Two checkouts that each insert their items first and lock
afterwards hold each other's lock whatever order they then ask in.
"""

import re
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.conf import settings
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from djmoney.money import Money

from cart.models.cart import Cart
from cart.models.item import CartItem
from order.enum.status import PaymentStatus
from order.models.stock_reservation import StockReservation
from order.services import OrderService
from order.stock import StockManager
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.assert_english


def _eur(amount: str) -> Money:
    return Money(amount, settings.DEFAULT_CURRENCY)


# ``values_list("pk")`` orders by its first output column, so the same
# ordering is spelled ``ORDER BY 1`` there and by name elsewhere.
ORDERED_BY_PK = re.compile(r'ORDER BY (?:1|"product_product"\."id") ASC')


def locked_tables(sql: str) -> set[str]:
    """The tables a ``FOR UPDATE`` statement takes row locks on.

    With ``OF`` only the named ones; without it every table in the
    statement, which is how a join locks rows the query never asked for.
    """
    match = re.search(r"\bFOR UPDATE(?: OF (?P<of>[^;]+?))?\s*$", sql)
    if match is None:
        return set()
    if match.group("of"):
        return set(re.findall(r'"(\w+)"', match.group("of")))
    return set(re.findall(r'(?:FROM|JOIN) "(\w+)"', sql))


def product_locks(queries) -> list[tuple[int, str, list[int] | None]]:
    """``(position, sql, ids)`` of every statement locking product rows.

    ``ids`` is None when the statement does not name them (a join).
    """
    locks = []
    for position, query in enumerate(queries):
        sql = query["sql"]
        if "product_product" not in locked_tables(sql):
            continue
        ids = None
        if match := re.search(r'"product_product"\."id" IN \(([\d, ]+)\)', sql):
            ids = [int(value) for value in match.group(1).split(",")]
        elif match := re.search(r'"product_product"\."id" = (\d+)', sql):
            ids = [int(match.group(1))]
        locks.append((position, sql, ids))
    return locks


def assert_pk_ordered(
    queries, expected_ids, *, walked_in_pk_order: bool = False
) -> None:
    """The first statement locks exactly ``expected_ids``, ascending, and
    runs before any row that references a product is inserted; every later
    product lock only re-locks a row it already holds.

    ``walked_in_pk_order`` also requires those later locks to follow pk
    order, which is the order the stock step walks the products in. Not
    every flow does: a leftover reservation is released after the paid
    lines, which is harmless because its row is held by then.
    """
    locks = product_locks(queries)
    assert locks, "no statement locked a product row"
    assert all(ids is not None for _pos, _sql, ids in locks), (
        "a statement locked product rows as a side effect of a join (no "
        "OF clause), in the order of its own rows: "
        f"{[sql for _pos, sql, ids in locks if ids is None]!r}"
    )

    first_position, first_sql, first_ids = locks[0]
    assert first_ids == sorted(expected_ids), (
        "the first product lock must take every product in ONE statement, "
        f"in pk order; got {first_ids} for {sorted(expected_ids)}"
    )
    assert ORDERED_BY_PK.search(first_sql), first_sql

    singles = [ids[0] for _pos, _sql, ids in locks[1:]]
    assert set(singles) <= set(first_ids), (
        "a product was locked for the first time AFTER the up-front "
        f"statement, out of pk order: {sorted(set(singles) - set(first_ids))}"
    )
    if walked_in_pk_order:
        assert singles == sorted(singles), (
            f"the per-product locks that follow ran out of pk order: {singles}"
        )

    first_reference = next(
        (
            position
            for position, query in enumerate(queries)
            if query["sql"].startswith('INSERT INTO "order_orderitem"')
        ),
        None,
    )
    assert first_reference is not None, "no OrderItem was inserted"
    assert first_position < first_reference, (
        "an OrderItem was inserted before the products were locked: the "
        "insert takes FOR KEY SHARE on each product, which blocks against "
        "the other checkout's FOR UPDATE in the opposite direction"
    )


@pytest.mark.django_db
class TestCheckoutLocksProductsInPkOrder:
    def setup_method(self):
        from country.factories import CountryFactory
        from tests.utils.shipping import enable_rate

        self.user = UserAccountFactory.create(num_addresses=0)
        self.country = CountryFactory.create()
        enable_rate(self.country)
        # Created in ascending pk order.
        self.products = [
            ProductFactory.create(price=_eur("10.00"), stock=50, active=True)
            for _ in range(3)
        ]
        self.shipping_address = {
            "first_name": "John",
            "last_name": "Doe",
            "email": "john@example.com",
            "street": "Main St",
            "street_number": "123",
            "city": "Athens",
            "zipcode": "12345",
            "country_id": self.country.alpha_2,
            "phone": "+306900000000",
            "shipping_kind": "home_delivery",
        }

    def _cart(self, lines_added_in: str) -> Cart:
        """Lines are listed newest first (``CartItem.Meta.ordering``), so
        adding them in ascending pk order makes the query, and the old
        lock order, DESCENDING."""
        cart = Cart.objects.create(user=None)
        products = (
            self.products
            if lines_added_in == "ascending"
            else list(reversed(self.products))
        )
        for product in products:
            CartItem.objects.create(cart=cart, product=product, quantity=1)
        return cart

    def _payment_first(self, cart):
        pay_way = PayWayFactory.create(
            provider_code="stripe", settlement=PaySettlement.ONLINE
        )
        provider = Mock()
        provider.get_payment_status.return_value = (PaymentStatus.PENDING, {})
        with patch("order.payment.get_payment_provider") as get_provider:
            get_provider.return_value = provider
            return OrderService.create_order_from_cart(
                cart=cart,
                shipping_address=self.shipping_address,
                payment_intent_id="pi_lock_order",
                pay_way=pay_way,
                user=None,
            )

    def _order_first(self, cart):
        pay_way = PayWayFactory.create(
            provider_code="cash_on_delivery",
            settlement=PaySettlement.COURIER_CASH,
        )
        return OrderService.create_order_from_cart_offline(
            cart=cart,
            shipping_address=self.shipping_address,
            pay_way=pay_way,
            user=None,
        )

    @pytest.mark.parametrize("lines_added_in", ["ascending", "descending"])
    @pytest.mark.parametrize("flow", ["_payment_first", "_order_first"])
    def test_product_locks_follow_pk_order_not_line_order(
        self, flow, lines_added_in
    ):
        cart = self._cart(lines_added_in)

        with CaptureQueriesContext(connection) as capture:
            getattr(self, flow)(cart)

        assert_pk_ordered(
            capture.captured_queries,
            [p.pk for p in self.products],
            walked_in_pk_order=True,
        )

    @pytest.mark.parametrize("flow", ["_payment_first", "_order_first"])
    def test_a_held_reservation_for_a_product_not_in_the_cart_is_locked_too(
        self, flow
    ):
        """Checkout releases it, and releasing takes that product's lock:
        left out of the up-front set it would be locked last, out of
        order."""
        in_cart, bystander = self.products[1], self.products[0]
        cart = Cart.objects.create(user=None)
        CartItem.objects.create(cart=cart, product=in_cart, quantity=1)
        StockReservation.objects.create(
            product=bystander,
            quantity=1,
            session_id=str(cart.uuid),
            expires_at=timezone.now() + timedelta(minutes=15),
        )

        with CaptureQueriesContext(connection) as capture:
            getattr(self, flow)(cart)

        assert_pk_ordered(capture.captured_queries, [bystander.pk, in_cart.pk])

    def test_free_gift_products_are_part_of_the_up_front_lock(self):
        """Gifts are decremented after the paid lines, so a gift product
        left out of the up-front set would be locked last, out of order."""
        gift = SimpleNamespace(product=self.products[0], quantity=1)
        paid = [SimpleNamespace(product_id=self.products[2].pk)]

        with CaptureQueriesContext(connection) as capture:
            OrderService._lock_order_products(
                cart_items=paid,
                reservations=[],
                promo_result=SimpleNamespace(gift_items=[gift]),
            )

        assert [
            ids for _p, _s, ids in product_locks(capture.captured_queries)
        ] == [sorted([self.products[0].pk, self.products[2].pk])]


@pytest.mark.django_db
class TestOtherMultiProductTransactionsLockInPkOrder:
    def setup_method(self):
        self.products = [
            ProductFactory.create(price=_eur("10.00"), stock=50, active=True)
            for _ in range(3)
        ]

    def test_cancel_locks_every_product_it_restores_up_front(self):
        """A cancel restores one product after another inside one
        transaction, so it takes several product locks too."""
        from order.enum.status import OrderStatus
        from order.models.item import OrderItem
        from order.models.order import Order

        order = Order.objects.create(
            user=None,
            status=OrderStatus.PENDING,
            email="a@example.com",
            first_name="A",
            last_name="B",
            street="S",
            street_number="1",
            city="C",
            zipcode="12345",
            phone="+306900000000",
        )
        # Taken in an order that is neither ascending nor descending.
        for product in (self.products[1], self.products[2], self.products[0]):
            OrderItem.objects.create(
                order=order, product=product, quantity=1, price=_eur("10.00")
            )
            StockManager.decrement_stock(
                product_id=product.pk, quantity=1, order_id=order.pk
            )

        with CaptureQueriesContext(connection) as capture:
            OrderService.cancel_order(
                order, refund_payment=False, customer_initiated=False
            )

        locks = product_locks(capture.captured_queries)
        assert locks[0][2] == sorted(p.pk for p in self.products), locks
        assert ORDERED_BY_PK.search(locks[0][1]), locks[0][1]
        singles = [ids[0] for _p, _s, ids in locks[1:]]
        assert singles == sorted(singles), singles

    def test_merging_a_guest_cart_locks_no_product_row(self):
        """The merge reads each product for its stock cap and writes none,
        so the join must not lock them (in the guest cart's line order)."""
        from django.contrib.auth.models import AnonymousUser
        from django.test import RequestFactory

        from cart.services import CartService

        user = UserAccountFactory.create(num_addresses=0)
        target = Cart.objects.create(user=user)
        guest = Cart.objects.create(user=None)
        for product in self.products:
            CartItem.objects.create(cart=guest, product=product, quantity=1)
        request = RequestFactory().get("/")
        request.user = user or AnonymousUser()
        request.META["HTTP_X_CART_ID"] = str(target.uuid)
        request.session = {}
        service = CartService(request=request)

        with CaptureQueriesContext(connection) as capture:
            service.merge_carts(guest)

        assert product_locks(capture.captured_queries) == []

    def test_the_bulk_discount_actions_lock_in_pk_order_on_the_product_table(
        self,
    ):
        from django.contrib.admin.sites import AdminSite
        from django.test import RequestFactory

        from product.admin import ProductAdmin
        from product.models.product import Product

        operator = UserAccountFactory.create(
            num_addresses=0, is_staff=True, is_superuser=True
        )
        request = RequestFactory().post("/admin/")
        request.user = operator
        request.session = {}
        admin = ProductAdmin(Product, AdminSite())
        # An ordering of the selection that is not pk order.
        selection = Product.objects.filter(
            pk__in=[p.pk for p in self.products]
        ).order_by("-pk")

        with (
            patch.object(ProductAdmin, "message_user"),
            CaptureQueriesContext(connection) as capture,
        ):
            admin.clear_discount(request, selection)

        locks = product_locks(capture.captured_queries)
        assert len(locks) == 1, locks
        sql = locks[0][1]
        assert ORDERED_BY_PK.search(sql), sql
        assert locked_tables(sql) == {"product_product"}, sql
