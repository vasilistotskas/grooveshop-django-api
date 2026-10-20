"""The cart list's order-source and checkout columns and filters.

The source column and filter read ``CartAttribution`` the way the order
list reads ``OrderAttribution`` (one shared filter base). The checkout
column names the order a cart's checkout already created while its
online payment is still open — the cart is left standing then, and the
order points back at it through ``metadata["cart_snapshot"]`` — and its
filter is named apart from the idle "Abandoned" one. Neither may add a
query per row.
"""

from __future__ import annotations

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import translation

from admin.filters import ACQUISITION_SOURCE_MAX_CHOICES
from cart.admin import CartAdmin, CartSourceFilter, CheckoutStateFilter
from cart.models import Cart, CartAttribution
from order.enum.attribution import OrderSourceType
from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory

User = get_user_model()

pytestmark = pytest.mark.django_db


def _admin():
    return CartAdmin(Cart, AdminSite())


def _listed(cart):
    """The cart as the changelist loads it."""
    return _admin().get_queryset(RequestFactory().get("/")).get(pk=cart.pk)


def _cart(source=None, source_type=OrderSourceType.SOCIAL):
    # Not ``CartFactory(is_guest=True)``: it get-or-creates on ``user``,
    # so every guest cart would be the same row.
    cart = Cart.objects.create(user=None)
    if source is not None:
        CartAttribution.objects.create(
            cart=cart, source=source, source_type=source_type
        )
    return cart


def _checkout(
    cart,
    *,
    settlement=PaySettlement.ONLINE,
    payment_status=PaymentStatus.PENDING,
):
    """An order built from *cart*, as both creation paths record it."""
    return OrderFactory(
        pay_way=PayWayFactory(settlement=settlement, active=True),
        status=OrderStatus.PENDING,
        payment_status=payment_status,
        num_order_items=0,
        metadata={
            "cart_snapshot": {"cart_id": cart.id, "cart_uuid": str(cart.uuid)}
        },
    )


class TestSourceColumn:
    def test_a_recorded_source_is_a_badge_keyed_by_type(self):
        cart = _cart("instagram")

        assert _admin().cart_source(_listed(cart)) == (
            OrderSourceType.SOCIAL,
            "Instagram",
        )

    def test_a_direct_cart_reads_as_direct(self):
        cart = _cart("", OrderSourceType.DIRECT)

        with translation.override("en"):
            _type, label = _admin().cart_source(_listed(cart))
            assert str(label) == "Direct"

    def test_a_cart_without_a_row_is_empty(self):
        assert _admin().cart_source(_listed(_cart())) is None


class TestCaptions:
    """A cart's source is the cart's, not an order's: the column header and
    the filter both said "Order source"."""

    def test_the_column_is_captioned_cart_source(self):
        column = _admin().cart_source
        with translation.override("en"):
            assert str(column.short_description) == "Cart source"

    def test_the_filter_is_captioned_cart_source(self):
        request = RequestFactory().get("/")
        flt = CartSourceFilter(request, {}, Cart, _admin())
        with translation.override("en"):
            assert str(flt.title) == "Cart source"


class TestSourceFilter:
    def _filter(self, value=None):
        params = {"order_source": [value]} if value else {}
        request = RequestFactory().get("/", params)
        return CartSourceFilter(request, params, Cart, _admin())

    def test_lookups_are_the_recorded_sources_by_name(self):
        _cart("instagram")
        _cart("blog.example.org", OrderSourceType.REFERRAL)
        _cart("", OrderSourceType.DIRECT)

        assert self._filter().lookups(None, _admin()) == [
            ("blog.example.org", "blog.example.org"),
            ("instagram", "Instagram"),
        ]

    def test_filters_on_the_source(self):
        instagram = _cart("instagram")
        _cart("google", OrderSourceType.SEARCH)
        _cart()

        filtered = self._filter("instagram").queryset(None, Cart.objects.all())

        assert list(filtered) == [instagram]


class TestSourceFilterIsBounded:
    def _filter(self):
        request = RequestFactory().get("/")
        return CartSourceFilter(request, {}, Cart, _admin())

    def _lookups(self):
        return self._filter().lookups(None, _admin())

    def test_offers_at_most_the_cap_in_one_query(self):
        for number in range(ACQUISITION_SOURCE_MAX_CHOICES + 10):
            _cart(f"typed-{number}", OrderSourceType.CAMPAIGN)

        source_filter = self._filter()  # unfold builds the choices here
        with CaptureQueriesContext(connection) as queries:
            lookups = source_filter.lookups(None, _admin())

        assert len(lookups) == ACQUISITION_SOURCE_MAX_CHOICES
        assert len(queries) == 1

    def test_known_channels_survive_the_cut_and_the_most_used_come_next(self):
        _cart("instagram")
        # Sorting before "instagram", so only the known-first rule keeps it.
        for number in range(ACQUISITION_SOURCE_MAX_CHOICES):
            _cart(f"a-typed-{number}", OrderSourceType.CAMPAIGN)
        for _ in range(3):
            _cart("popular.example.org", OrderSourceType.REFERRAL)

        sources = {value for value, _label in self._lookups()}

        assert {"instagram", "popular.example.org"} <= sources
        assert len(sources) == ACQUISITION_SOURCE_MAX_CHOICES


class TestCheckoutColumn:
    def _text(self, cart):
        with translation.override("en"):
            return str(_admin().checkout_state(_listed(cart)))

    def test_names_and_links_the_order_awaiting_payment(self):
        cart = _cart()
        order = _checkout(cart)

        text = self._text(cart)

        assert f"Checkout started, awaiting payment: order #{order.pk}" in text
        assert reverse("admin:order_order_change", args=[order.pk]) in text

    def test_a_cart_without_a_checkout_is_empty(self):
        assert _admin().checkout_state(_listed(_cart())) is None

    @pytest.mark.parametrize(
        "settings",
        [
            {"payment_status": PaymentStatus.COMPLETED},
            {"payment_status": PaymentStatus.CANCELED},
            {"settlement": PaySettlement.COURIER_CASH},
        ],
        ids=["paid", "canceled", "cash-on-delivery"],
    )
    def test_an_order_that_is_not_waiting_for_online_payment_is_not_shown(
        self, settings
    ):
        cart = _cart()
        _checkout(cart, **settings)

        assert _admin().checkout_state(_listed(cart)) is None

    def test_another_carts_order_is_not_shown(self):
        _checkout(_cart())

        assert _admin().checkout_state(_listed(_cart())) is None

    def test_the_latest_order_is_the_one_named(self):
        cart = _cart()
        _checkout(cart)
        latest = _checkout(cart)

        assert f"order #{latest.pk}" in self._text(cart)


class TestCheckoutFilter:
    def _filter(self, value=None):
        params = {"checkout": [value]} if value else {}
        request = RequestFactory().get("/", params)
        return CheckoutStateFilter(request, params, Cart, _admin())

    def _queryset(self, value):
        return self._filter(value).queryset(
            None, _admin().get_queryset(RequestFactory().get("/"))
        )

    def test_keeps_the_carts_whose_checkout_awaits_payment(self):
        waiting = _cart()
        _checkout(waiting)
        paid = _cart()
        _checkout(paid, payment_status=PaymentStatus.COMPLETED)
        _cart()

        assert list(self._queryset("awaiting_payment")) == [waiting]

    def test_no_value_keeps_every_cart(self):
        carts = {_cart().pk, _cart().pk}

        assert {c.pk for c in self._queryset(None)} == carts

    def test_is_named_apart_from_the_idle_abandoned_filter(self):
        with translation.override("en"):
            (_value, label), *rest = self._filter().lookups(None, _admin())

        assert not rest
        assert "Abandoned" not in str(label)


class TestCartChangelist:
    @pytest.fixture
    def client(self):
        user = User.objects.create_superuser(
            username="boss", email="boss@example.com", password="x"
        )
        client = Client()
        client.force_login(
            user, backend="tenant.auth_backends.PlatformStaffBackend"
        )
        return client

    def _get(self, client, **params):
        return client.get(reverse("admin:cart_cart_changelist"), params)

    def test_filters_by_source_type_and_by_checkout(self, client):
        social = _cart("instagram")
        _cart("google", OrderSourceType.SEARCH)
        waiting = _cart("instagram")
        _checkout(waiting)

        by_type = self._get(
            client, attribution__source_type__exact=OrderSourceType.SOCIAL
        )
        by_checkout = self._get(client, checkout="awaiting_payment")

        assert by_type.status_code == 200
        assert {c.pk for c in by_type.context["cl"].result_list} == {
            social.pk,
            waiting.pk,
        }
        assert by_checkout.status_code == 200
        assert [c.pk for c in by_checkout.context["cl"].result_list] == [
            waiting.pk
        ]

    def test_the_changelist_adds_no_query_per_row(self, client):
        def row():
            cart = _cart("instagram")
            _checkout(cart)
            _checkout(cart)

        row()
        _cart()  # no attribution row, no checkout
        self._get(client)  # warm per-request caches
        with CaptureQueriesContext(connection) as few:
            assert self._get(client).status_code == 200

        for _ in range(4):
            row()
        self._get(client)
        with CaptureQueriesContext(connection) as many:
            assert self._get(client).status_code == 200

        assert len(many) == len(few)
