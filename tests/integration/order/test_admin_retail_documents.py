"""The order's "Invoicing & myDATA" actions and its Receipt card.

Retail receipts are issued through a fiscal device (ΦΗΜ) or a licensed
provider (ν.4308/2014 art. 12 §8, §10); the platform issues them over the
myDATA ERP channel only for a store that records its ΦΗΜ exemption
(``MyDataConfig.is_ready``). Every other store issues its receipts outside
the platform, so the order page offers no myDATA action, refuses a forged
one, and its Receipt card says where the receipt comes from.
"""

from __future__ import annotations

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied
from django.test import Client, RequestFactory
from django.urls import reverse

from order.admin import OrderAdmin
from order.enum.status import OrderStatus
from order.factories import OrderFactory
from order.models.invoice import Invoice, InvoiceCounter
from order.models.order import Order
from tests.utils.mydata import issuable, mydata_enabled
from user.factories import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

RETAIL_DOCUMENT_ACTIONS = (
    "issue_receipt_now",
    "regenerate_invoice",
    "send_invoice_to_mydata_now",
    "issue_credit_documents_now",
    "cancel_mydata_invoice_now",
)


@pytest.fixture
def order_admin():
    return OrderAdmin(Order, AdminSite())


@pytest.fixture
def staff_user():
    return UserAccountFactory(is_staff=True, is_superuser=True)


def _request(staff_user):
    request = RequestFactory().post("/admin/order/order/")
    request.user = staff_user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


def _messages(request) -> list[str]:
    return [str(m) for m in get_messages(request)]


def _shipped_order():
    return issuable(OrderFactory(status=OrderStatus.PROCESSING))


class TestThePermission:
    def test_a_store_that_issues_outside_the_platform_has_none(
        self, order_admin, staff_user
    ):
        order = _shipped_order()

        assert not order_admin.has_issuing_permission(
            _request(staff_user), object_id=order.pk
        )

    def test_a_store_the_platform_issues_for_has_it(
        self, order_admin, staff_user
    ):
        order = _shipped_order()

        with mydata_enabled(auto_submit=False):
            assert order_admin.has_issuing_permission(
                _request(staff_user), object_id=order.pk
            )

    def test_every_action_asks_for_it(self, order_admin):
        for name in RETAIL_DOCUMENT_ACTIONS:
            method = getattr(OrderAdmin, name)
            assert "issuing" in method.allowed_permissions, name

    @pytest.mark.parametrize("name", RETAIL_DOCUMENT_ACTIONS)
    def test_a_forged_request_is_refused_and_creates_nothing(
        self, order_admin, staff_user, name
    ):
        order = _shipped_order()
        counters = list(InvoiceCounter.objects.values_list("next_number"))

        with pytest.raises(PermissionDenied):
            getattr(order_admin, name)(_request(staff_user), object_id=order.pk)

        assert not Invoice.objects.filter(order=order).exists()
        assert list(InvoiceCounter.objects.values_list("next_number")) == (
            counters
        )


class TestRegenerate:
    def test_an_order_without_a_receipt_has_nothing_to_regenerate(
        self, order_admin, staff_user
    ):
        """Regenerating re-renders a receipt; it never issues one, so no
        number is allocated for an order that has none."""
        order = _shipped_order()
        request = _request(staff_user)

        with mydata_enabled(auto_submit=False):
            order_admin.regenerate_invoice(request, object_id=order.pk)

        assert not Invoice.objects.filter(order=order).exists()
        assert not InvoiceCounter.objects.exists()
        assert _messages(request) == [
            f"Order #{order.pk} has no receipt to regenerate."
        ]


class TestTheOrderPage:
    @pytest.fixture
    def client(self):
        client = Client()
        client.force_login(
            UserAccountFactory(admin=True),
            backend="tenant.auth_backends.PlatformStaffBackend",
        )
        return client

    def _page(self, client, order) -> str:
        response = client.get(
            reverse("admin:order_order_change", args=[order.pk])
        )
        assert response.status_code == 200
        return response.content.decode()

    def test_no_myDATA_action_and_the_card_says_why(self, client):
        order = _shipped_order()

        html = self._page(client, order)

        for name in RETAIL_DOCUMENT_ACTIONS:
            assert name not in html, name
        assert "Invoicing &amp; myDATA" not in html
        assert "Issued by the store outside the platform" in html

    def test_a_store_the_platform_issues_for_gets_the_actions(self, client):
        order = _shipped_order()

        with mydata_enabled(auto_submit=False):
            html = self._page(client, order)

        for name in RETAIL_DOCUMENT_ACTIONS:
            assert name in html, name
        assert "Issued by the store outside the platform" not in html
