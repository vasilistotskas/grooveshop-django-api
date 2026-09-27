"""The order list's payment-method and order-source columns and filters.

The payment-method column names the PSP when the pay way charges
through one — not the pay way's own key, which is ``CREDIT_CARD`` for
the seeded Viva pay way — and the method the shopper picked when there
is no PSP (cash on delivery). The source column is a badge coloured by
source type. Neither may add a query per row.
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

from order.admin import OrderAdmin, OrderSourceFilter
from order.enum.attribution import OrderSourceType
from order.factories import OrderAttributionFactory, OrderFactory
from order.models.order import Order
from pay_way.enum.pay_way import PayWayEnum
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory

User = get_user_model()

pytestmark = pytest.mark.django_db


def _admin():
    return OrderAdmin(Order, AdminSite())


def _listed(order):
    """The order as the changelist loads it."""
    return _admin().get_queryset(RequestFactory().get("/")).get(pk=order.pk)


def _order(*, provider_code, settlement, pay_way_key):
    order = OrderFactory(
        pay_way=PayWayFactory(
            provider_code=provider_code, settlement=settlement, active=True
        ),
        num_order_items=0,
    )
    # The snapshot follows the PayWay row's key; pin the one each case
    # is about.
    Order.objects.filter(pk=order.pk).update(pay_way_key=pay_way_key)
    return order


class TestPaymentMethodColumn:
    def test_viva_reads_as_viva_not_as_its_credit_card_key(self):
        order = _order(
            provider_code="viva_wallet",
            settlement=PaySettlement.ONLINE,
            pay_way_key=PayWayEnum.CREDIT_CARD,
        )

        with translation.override("en"):
            assert str(_admin().payment_method_label(_listed(order))) == (
                "Viva Wallet"
            )

    def test_stripe_reads_as_stripe(self):
        order = _order(
            provider_code="stripe",
            settlement=PaySettlement.ONLINE,
            pay_way_key=PayWayEnum.CREDIT_CARD,
        )

        with translation.override("en"):
            assert str(_admin().payment_method_label(_listed(order))) == (
                "Stripe"
            )

    def test_cash_on_delivery_reads_as_the_method_chosen(self):
        order = _order(
            provider_code="",
            settlement=PaySettlement.COURIER_CASH,
            pay_way_key=PayWayEnum.PAY_ON_DELIVERY,
        )

        with translation.override("en"):
            assert str(_admin().payment_method_label(_listed(order))) == (
                "Pay On Delivery"
            )

    def test_no_pay_way_and_no_snapshot_is_empty(self):
        order = OrderFactory(pay_way=None, num_order_items=0)
        Order.objects.filter(pk=order.pk).update(pay_way_key="")

        assert _admin().payment_method_label(_listed(order)) is None


class TestOrderSourceColumn:
    def test_a_recorded_source_is_a_badge_keyed_by_type(self):
        attribution = OrderAttributionFactory(
            source_type=OrderSourceType.SOCIAL, source="instagram"
        )

        assert _admin().order_source(_listed(attribution.order)) == (
            OrderSourceType.SOCIAL,
            "Instagram",
        )

    def test_a_direct_order_reads_as_direct(self):
        attribution = OrderAttributionFactory(
            source_type=OrderSourceType.DIRECT, source=""
        )

        with translation.override("en"):
            _type, label = _admin().order_source(_listed(attribution.order))
            assert str(label) == "Direct"

    def test_an_order_from_before_attribution_is_empty(self):
        order = OrderFactory(num_order_items=0)

        assert _admin().order_source(_listed(order)) is None
        assert _admin().attribution_source(_listed(order)) is None

    def test_the_change_form_fields_read_the_row(self):
        attribution = OrderAttributionFactory(
            source_type=OrderSourceType.REFERRAL,
            source="blog.example.org",
            referrer_host="blog.example.org",
            landing_path="/products/7",
            medium="",
        )
        admin = _admin()
        order = _listed(attribution.order)

        assert admin.attribution_source(order) == "blog.example.org"
        assert admin.attribution_referrer_host(order) == "blog.example.org"
        assert admin.attribution_landing_path(order) == "/products/7"
        assert admin.attribution_medium(order) is None


class TestOrderSourceFilter:
    def _filter(self, value=None):
        # The changelist hands filters list values, as QueryDict does.
        params = {"order_source": [value]} if value else {}
        request = RequestFactory().get("/", params)
        return OrderSourceFilter(request, params, Order, _admin())

    def test_lookups_are_the_recorded_sources_by_name(self):
        OrderAttributionFactory(
            source_type=OrderSourceType.SOCIAL, source="instagram"
        )
        OrderAttributionFactory(
            source_type=OrderSourceType.CAMPAIGN, source="instagram"
        )
        OrderAttributionFactory(
            source_type=OrderSourceType.REFERRAL, source="blog.example.org"
        )
        OrderAttributionFactory(source_type=OrderSourceType.DIRECT, source="")

        lookups = self._filter().lookups(None, _admin())

        assert lookups == [
            ("blog.example.org", "blog.example.org"),
            ("instagram", "Instagram"),
        ]

    def test_filters_on_the_source(self):
        instagram = OrderAttributionFactory(source="instagram").order
        OrderAttributionFactory(source="google")
        OrderFactory(num_order_items=0)

        filtered = self._filter("instagram").queryset(None, Order.objects.all())

        assert list(filtered) == [instagram]


class TestOrderChangelist:
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
        return client.get(reverse("admin:order_order_changelist"), params)

    def test_filters_by_source_type(self, client):
        social = OrderAttributionFactory(
            source_type=OrderSourceType.SOCIAL
        ).order
        search = OrderAttributionFactory(
            source_type=OrderSourceType.SEARCH, source="google"
        ).order

        response = self._get(
            client, attribution__source_type__exact=OrderSourceType.SOCIAL
        )

        assert response.status_code == 200
        pks = {order.pk for order in response.context["cl"].result_list}
        assert social.pk in pks
        assert search.pk not in pks

    def test_the_new_columns_add_no_query_per_row(self, client):
        # Rows carry a line item: an item-less order has no
        # ``items_total`` annotation value and falls back to a per-row
        # SUM, which is the order-total column's cost, not these ones.
        def row():
            OrderAttributionFactory(
                order=OrderFactory(
                    pay_way=PayWayFactory(
                        provider_code="viva_wallet",
                        settlement=PaySettlement.ONLINE,
                    ),
                    num_order_items=1,
                )
            )

        row()
        OrderFactory(num_order_items=1)  # no attribution row
        self._get(client)  # warm per-request caches
        with CaptureQueriesContext(connection) as few:
            assert self._get(client).status_code == 200

        for _ in range(4):
            row()
        with CaptureQueriesContext(connection) as many:
            assert self._get(client).status_code == 200

        assert len(many) == len(few)
