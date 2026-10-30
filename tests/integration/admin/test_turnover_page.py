"""The turnover page and its CSV: who may open them, and what they show.

The definition of the figures is pinned in ``tests/unit/order/test_turnover.py``;
this is the page around it: the permission (store ADMIN and OWNER, not
STAFF), the year, the export, and the sidebar entry.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import translation
from djmoney.money import Money

from admin.displays import money
from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models import Order
from product.factories import ProductFactory
from tenant.models import TenantMembershipRole
from tests.utils.staff import (
    bind_store_tenant,
    store_staff,
    store_tenant,
    unbind_store_tenant,
)
from vat.factories import VatFactory

pytestmark = pytest.mark.django_db

D = Decimal


@pytest.fixture
def tenant():
    tenant = store_tenant("turnover_page")
    previous = bind_store_tenant(tenant)
    yield tenant
    unbind_store_tenant(previous)


def _client(tenant, role) -> Client:
    client = Client()
    client.force_login(
        store_staff(tenant, role=role),
        backend="tenant.auth_backends.PlatformStaffBackend",
    )
    return client


@pytest.fixture
def sale():
    """One collected order of 100.00 net (124.00 at 24%) in March 2026."""
    order = OrderFactory(
        num_order_items=0,
        status=OrderStatus.DELIVERED,
        payment_status=PaymentStatus.COMPLETED,
        shipping_price=Money(D("10.00"), "EUR"),
        payment_method_fee=Money(D("0"), "EUR"),
    )
    order.items.all().delete()
    order.items.create(
        product=ProductFactory(vat=VatFactory(value=D("24"))),
        price=Money(D("124.00"), "EUR"),
        quantity=1,
        vat_rate=D("24"),
    )
    Order.objects.filter(pk=order.pk).update(
        created_at=datetime(2026, 3, 15, 12, tzinfo=UTC)
    )
    return order


@pytest.mark.parametrize("name", ["admin:turnover", "admin:turnover-csv"])
@pytest.mark.parametrize(
    ("role", "status"),
    [
        (TenantMembershipRole.STAFF, 403),
        (TenantMembershipRole.ADMIN, 200),
        (TenantMembershipRole.OWNER, 200),
    ],
)
def test_the_report_needs_the_turnover_permission(tenant, role, status, name):
    response = _client(tenant, role).get(reverse(name))

    assert response.status_code == status


def test_the_page_lists_the_months_of_the_year_with_a_total(tenant, sale):
    response = _client(tenant, TenantMembershipRole.ADMIN).get(
        reverse("admin:turnover"), {"year": 2026}
    )
    # The admin is served in Greek, which writes 100,00.
    with translation.override("el"):
        expected = money(D("100"))
        zero = money(D("0"))

    assert response.status_code == 200
    table = response.context["table"]
    assert len(table["rows"]) == 13
    march = table["rows"][2]
    assert march[1:4] == [expected, zero, expected]
    assert march[4] == 1
    total = table["rows"][-1]
    assert total[1:] == [expected, zero, expected, 1]
    assert response.context["year"] == 2026


def test_the_year_selector_offers_the_years_with_sales(tenant, sale):
    response = _client(tenant, TenantMembershipRole.ADMIN).get(
        reverse("admin:turnover"), {"year": 2026}
    )

    labels = [year["label"] for year in response.context["years"]]
    assert labels[-1] == "2026"
    assert labels == sorted(labels, reverse=True)
    assert [y["active"] for y in response.context["years"]].count(True) == 1


@pytest.mark.parametrize("year", ["abc", "1999", "20260"])
@pytest.mark.parametrize("name", ["admin:turnover", "admin:turnover-csv"])
def test_a_year_that_is_not_offered_is_a_bad_request(tenant, sale, name, year):
    response = _client(tenant, TenantMembershipRole.ADMIN).get(
        reverse(name), {"year": year}
    )

    assert response.status_code == 400


def test_the_csv_holds_the_months_and_the_total(tenant, sale):
    response = _client(tenant, TenantMembershipRole.OWNER).get(
        reverse("admin:turnover-csv"), {"year": 2026}
    )

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    assert 'filename="turnover-2026.csv"' in response["Content-Disposition"]
    body = response.content.decode("utf-8-sig")
    assert response.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(body)))
    assert len(rows) == 14
    assert rows[3] == ["2026-03", "100.00", "0.00", "100.00", "1"]
    assert rows[-1][1:] == ["100.00", "0.00", "100.00", "1"]


def test_the_sidebar_offers_the_report_to_those_who_may_open_it(tenant):
    for role, offered in (
        (TenantMembershipRole.ADMIN, True),
        (TenantMembershipRole.STAFF, False),
    ):
        response = _client(tenant, role).get(reverse("admin:index"))
        link = reverse("admin:turnover")

        assert (link in response.content.decode()) is offered
