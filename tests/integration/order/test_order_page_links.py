"""Where a link to an order sends its shopper (``order.storefront_links``).

Two storefront pages show an order: the account's order page, which needs
its owner signed in, and the order confirmation, which the order's uuid
opens without a sign-in. A guest has no account to sign in to, and on a
store with customer accounts off nobody does (``tenant.customer_accounts``,
where the storefront answers 404 for every ``/account/**`` path). So the
account page is the target only for an account holder's order on a store
that signs its shoppers in, and every email that links to an order follows
that rule. The uuid then has to open such an order, which these tests also
pin on the order API.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core import mail
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from order.accounting.invoicing import _verification_url
from order.enum.status import OrderStatus
from order.factories.order import OrderFactory
from order.models.invoice import Invoice
from order.serializers.invoice import InvoiceDownloadResponseSerializer
from order.storefront_links import order_page_path, order_page_url
from order.tasks import send_payment_failed_email
from shipping.models import ShippingProvider
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.tasks import acs_send_arrival_notification
from shipping_boxnow.factories import (
    BoxNowParcelEventFactory,
    BoxNowShipmentFactory,
)
from shipping_boxnow.tasks import boxnow_send_arrival_notification
from tests.utils.emails import open_email_gates  # noqa: F401
from tests.utils.store_settings import switch_store_settings
from user.factories.account import UserAccountFactory

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("open_email_gates"),
]

BASE = "https://shop.example"


@pytest.fixture(autouse=True)
def _storefront(settings):
    settings.NUXT_BASE_URL = BASE
    mail.outbox = []


@pytest.fixture
def store(bind_tenant_and_schema):
    """A store's host whose customer accounts switch the test sets."""

    def _store(*, accounts: bool) -> None:
        switch_store_settings(CUSTOMER_ACCOUNTS_ENABLED=accounts)
        bind_tenant_and_schema(
            SimpleNamespace(
                schema_name="a_store",
                available_locales=["el", "en"],
                default_locale="el",
            )
        )

    return _store


def _guest_order(**fields):
    return OrderFactory(user=None, num_order_items=0, **fields)


def _account_order(**fields):
    return OrderFactory(
        user=UserAccountFactory(num_addresses=0), num_order_items=0, **fields
    )


def _guest_page(order) -> str:
    return f"/checkout/success/{order.uuid}"


def _account_page(order) -> str:
    return f"/account/orders/{order.id}"


def _body(message) -> str:
    return message.body + "".join(
        content for content, _mime in getattr(message, "alternatives", [])
    )


# --- the rule ------------------------------------------------------------


def test_a_guest_order_links_to_its_confirmation(store):
    store(accounts=True)
    order = _guest_order()

    assert order_page_path(order) == _guest_page(order)


def test_an_account_order_links_to_the_account_page(store):
    store(accounts=True)
    order = _account_order()

    assert order_page_path(order) == _account_page(order)


def test_an_account_order_of_a_store_without_accounts_links_by_uuid(store):
    store(accounts=False)
    order = _account_order()

    assert order_page_path(order) == _guest_page(order)


@pytest.mark.parametrize(("language", "prefix"), [("en", "/en"), ("el", "")])
def test_the_url_opens_in_the_language_asked_for(store, language, prefix):
    store(accounts=True)
    order = _guest_order()

    assert order_page_url(order, language=language) == (
        f"{BASE}{prefix}{_guest_page(order)}"
    )


# --- the uuid opens what the links send to it ---------------------------


@pytest.mark.parametrize("accounts", [True, False], ids=["on", "off"])
def test_the_uuid_opens_an_account_order_only_without_accounts(store, accounts):
    """On a store that signs no shopper in, an account holder's order is
    read like a guest's: by its uuid. Where accounts are on it still needs
    its owner."""
    store(accounts=accounts)
    order = _account_order()

    response = APIClient().get(
        reverse("order-retrieve-by-uuid", kwargs={"uuid": str(order.uuid)})
    )

    expected = status.HTTP_403_FORBIDDEN if accounts else status.HTTP_200_OK
    assert response.status_code == expected, response.data


def test_a_wrong_uuid_still_opens_nothing_without_accounts(store):
    store(accounts=False)
    order = _account_order()

    response = APIClient().get(
        reverse("order-invoice", kwargs={"pk": order.id}),
        {"uuid": "00000000-0000-4000-8000-000000000000"},
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_its_owner_cancels_by_uuid_without_accounts(store):
    store(accounts=False)
    order = _account_order(status=OrderStatus.PENDING)

    with patch(
        "order.services.OrderService.cancel_order",
        return_value=(order, None),
    ) as cancel:
        response = APIClient().post(
            reverse("order-cancel", kwargs={"pk": order.id})
            + f"?uuid={order.uuid}",
            {},
            format="json",
        )

    assert response.status_code == status.HTTP_200_OK, response.data
    cancel.assert_called_once()


@pytest.mark.parametrize("accounts", [True, False], ids=["on", "off"])
def test_the_receipt_link_carries_the_uuid_only_without_accounts(
    store, accounts
):
    """The receipt's download link is opened the way the order is: with
    the uuid where the order is served as a guest's."""
    store(accounts=accounts)
    order = _account_order()
    invoice = Invoice(order=order)

    with patch.object(Invoice, "has_document", return_value=True):
        url = InvoiceDownloadResponseSerializer().get_download_url(invoice)

    assert (f"uuid={order.uuid}" in url) is not accounts


# --- every email that links to the order ----------------------------------


@pytest.mark.parametrize(
    ("make", "accounts", "page"),
    [
        (_guest_order, True, _guest_page),
        (_account_order, True, _account_page),
        (_account_order, False, _guest_page),
    ],
    ids=["guest", "account", "account-without-accounts"],
)
class TestOrderEmails:
    """Sent through the queue, as the store sends them: the task runs on
    the store's schema (``tenant.celery.TenantTask``)."""

    def test_payment_failed(self, store, make, accounts, page):
        store(accounts=accounts)
        order = make(language_code="en")

        assert send_payment_failed_email.delay(order.id).get() is True

        assert f"{BASE}/en{page(order)}" in _body(mail.outbox[-1])

    def test_acs_out_for_delivery(self, store, make, accounts, page):
        store(accounts=accounts)
        order = make(language_code="en")
        shipment = AcsShipmentFactory(order=order, with_voucher=True)
        ShippingProvider.objects.filter(code="acs").update(is_active=True)

        acs_send_arrival_notification.delay(shipment.id)

        body = _body(mail.outbox[-1])
        assert f"{BASE}/en{page(order)}" in body
        assert "/account/orders/" not in body or page is _account_page

    def test_boxnow_parcel_at_locker(self, store, make, accounts, page):
        store(accounts=accounts)
        order = make(language_code="en")
        event = BoxNowParcelEventFactory(
            shipment=BoxNowShipmentFactory(order=order, with_parcel=True)
        )
        ShippingProvider.objects.filter(code="boxnow").update(is_active=True)

        boxnow_send_arrival_notification.delay(event.id)

        body = _body(mail.outbox[-1])
        assert f"{BASE}/en{page(order)}" in body
        assert "/account/orders/" not in body or page is _account_page

    def test_invoice_qr(self, store, make, accounts, page):
        store(accounts=accounts)
        order = make(language_code="en")

        assert _verification_url(order, language="en") == (
            f"{BASE}/en{page(order)}"
        )
