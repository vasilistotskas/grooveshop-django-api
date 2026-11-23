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
from tenant.enum import TenantMembershipRole
from tests.utils.mydata import issuable, mydata_enabled
from tests.utils.staff import (
    bind_store_tenant,
    stamp_platform_identity,
    store_staff,
    store_tenant,
    unbind_store_tenant,
)
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


class TestCancelACreditInMyData:
    """ "Cancel in myDATA (issued in error)" on a credit document's own
    page (#653). The register refuses ``change`` to everyone, so while the
    action asked for it nobody was offered it, a superuser included. It
    asks for the store's own permission on credit documents, and only
    where the platform issues the store's documents, like the order's
    "Cancel receipt in myDATA". One credit at a time: a cancellation at
    AADE cannot be undone, so no selection can cancel a batch."""

    @pytest.fixture
    def tenant(self):
        tenant = store_tenant("credit_cancel")
        previous = bind_store_tenant(tenant)
        yield tenant
        unbind_store_tenant(previous)

    @staticmethod
    def _credit(*, mark=400000000000002, status=None, number="CRD-2026-000001"):
        from datetime import date

        from order.accounting.invoicing import generate_invoice
        from order.models.retail_credit import CreditReason, RetailCredit
        from tests.utils.mydata import registered

        order = _shipped_order()
        with mydata_enabled(auto_submit=False):
            invoice = registered(generate_invoice(order))
        type(invoice).objects.filter(pk=invoice.pk).update(
            mydata_mark=500000000000000 + order.pk
        )
        credit = RetailCredit.objects.create(
            order=order,
            invoice=invoice,
            reason=CreditReason.SETTLEMENT,
            credit_number=number,
            issue_date=date(2026, 3, 20),
            mydata_mark=mark,
        )
        if status is not None:
            RetailCredit.objects.filter(pk=credit.pk).update(
                mydata_status=status
            )
            credit.refresh_from_db()
        return credit

    @staticmethod
    def _client(user) -> Client:
        from tenant.auth_backends import PLATFORM_STAFF_BACKEND_PATH

        client = Client(raise_request_exception=False)
        client.force_login(user, backend=PLATFORM_STAFF_BACKEND_PATH)
        return client

    @staticmethod
    def _url(credit) -> str:
        return reverse(
            "admin:order_retailcredit_cancel_in_mydata", args=[credit.pk]
        )

    def _post(self, tenant, role, credit, *, ready=True):
        from unittest.mock import patch

        client = self._client(store_staff(tenant, role=role))
        with (
            mydata_enabled(ready=ready),
            patch("order.tasks.cancel_mydata_credit.delay") as cancel,
        ):
            response = client.post(self._url(credit))
        return response, cancel

    @pytest.mark.parametrize(
        "role", [TenantMembershipRole.STAFF, TenantMembershipRole.ADMIN]
    )
    def test_a_store_role_schedules_the_cancel_of_a_registered_credit(
        self, tenant, role
    ):
        credit = self._credit()

        response, cancel = self._post(tenant, role, credit)

        assert response.status_code == 302
        cancel.assert_called_once_with(credit.pk)

    def test_the_success_message_names_the_credit(self, tenant):
        credit = self._credit()
        client = self._client(
            store_staff(tenant, role=TenantMembershipRole.STAFF)
        )
        from unittest.mock import patch

        with mydata_enabled(), patch("order.tasks.cancel_mydata_credit.delay"):
            response = client.post(self._url(credit), follow=True)

        assert [str(m) for m in response.context["messages"]] == [
            (
                "Scheduled myDATA cancellation for credit document "
                f"{credit.credit_number} (MARK {credit.mydata_mark})."
            )
        ]

    def test_no_bulk_action_is_left_on_the_changelist(self, tenant):
        from order.admin import RetailCreditAdmin
        from order.models.retail_credit import RetailCredit

        user = store_staff(tenant, role=TenantMembershipRole.ADMIN)

        with mydata_enabled():
            actions = RetailCreditAdmin(RetailCredit, AdminSite()).get_actions(
                _request(user)
            )

        assert "cancel_in_mydata" not in actions

    def test_staff_without_a_role_in_the_store_is_refused(self, tenant):
        from order.admin import RetailCreditAdmin
        from order.models.retail_credit import RetailCredit

        credit = self._credit()
        outsider = stamp_platform_identity(UserAccountFactory(is_staff=True))
        model_admin = RetailCreditAdmin(RetailCredit, AdminSite())

        with mydata_enabled():
            assert not model_admin.has_cancel_in_mydata_permission(
                _request(outsider), str(credit.pk)
            )
            assert model_admin.has_cancel_in_mydata_permission(
                _request(store_staff(tenant)), str(credit.pk)
            )

    def test_refused_where_the_store_issues_outside_the_platform(self, tenant):
        credit = self._credit()

        response, cancel = self._post(
            tenant, TenantMembershipRole.ADMIN, credit, ready=False
        )

        assert response.status_code == 403
        cancel.assert_not_called()

    def test_a_credit_with_no_mark_is_refused_with_a_message(self, tenant):
        from order.admin import RetailCreditAdmin
        from order.models.retail_credit import RetailCredit

        credit = self._credit(mark=None)
        request = _request(store_staff(tenant, role=TenantMembershipRole.STAFF))

        from unittest.mock import patch

        with (
            mydata_enabled(),
            patch("order.tasks.cancel_mydata_credit.delay") as cancel,
        ):
            RetailCreditAdmin(RetailCredit, AdminSite()).cancel_in_mydata(
                request, object_id=credit.pk
            )

        cancel.assert_not_called()
        assert _messages(request) == [
            (
                f"Credit document {credit.credit_number} has no myDATA MARK "
                "- nothing to cancel."
            )
        ]

    def test_an_already_canceled_credit_is_refused_with_a_message(self, tenant):
        from order.admin import RetailCreditAdmin
        from order.models.invoice import MyDataStatus
        from order.models.retail_credit import RetailCredit

        credit = self._credit(status=MyDataStatus.CANCELED)
        request = _request(store_staff(tenant, role=TenantMembershipRole.STAFF))

        from unittest.mock import patch

        with (
            mydata_enabled(),
            patch("order.tasks.cancel_mydata_credit.delay") as cancel,
        ):
            RetailCreditAdmin(RetailCredit, AdminSite()).cancel_in_mydata(
                request, object_id=credit.pk
            )

        cancel.assert_not_called()
        assert _messages(request) == [
            (
                f"Credit document {credit.credit_number} is already "
                "canceled in myDATA."
            )
        ]
