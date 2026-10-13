"""Per-tenant fields read through ``schema_context`` see the tenant's row.

``schema_context(name)`` binds a bare django-tenants ``FakeTenant`` that
carries only ``schema_name``. Every commit hook that re-enters its schema
(``order/signals/handlers.py``), management command and webhook loop runs
that way, and ``tenant.credentials`` used to read its fields straight off
``connection.tenant`` -- so a store with credentials on its row read as
unconfigured and the work was skipped without an error. Verified on
production 2026-10-13: shipping an order with myDATA enabled never
dispatched the receipt.
"""

from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django_tenants.utils import schema_context

from order.enum.document_type import OrderDocumentTypeEnum
from order.enum.status import OrderStatus
from order.factories import OrderFactory
from order.signals.handlers import handle_order_shipped
from order.tasks import generate_order_invoice
from tenant import credentials
from tenant.membership import bound_tenant, tenant_plan_allows
from tests.utils.staff import store_tenant

pytestmark = pytest.mark.django_db

FAMILIES = {
    "viva": (
        credentials.viva_wallet_credentials,
        {
            "viva_wallet_merchant_id": "merchant",
            "viva_wallet_api_key": "api-key",
            "viva_wallet_client_id": "client-id",
            "viva_wallet_client_secret": "client-secret",
            "viva_wallet_webhook_verification_key": "webhook-key",
            "viva_wallet_source_code": "1234",
            "viva_wallet_live_mode": True,
        },
        {
            "merchant_id": "merchant",
            "api_key": "api-key",
            "client_id": "client-id",
            "client_secret": "client-secret",
            "webhook_verification_key": "webhook-key",
            "source_code": "1234",
            "live_mode": True,
        },
    ),
    "stripe": (
        credentials.stripe_credentials,
        {
            "stripe_secret_key": "sk_live_x",
            "stripe_publishable_key": "pk_live_x",
        },
        {
            "secret_key": "sk_live_x",
            "publishable_key": "pk_live_x",
            "live_mode": True,
        },
    ),
    "acs": (
        credentials.acs_credentials,
        {
            "acs_api_key": "key",
            "acs_company_id": "company",
            "acs_company_password": "company-pw",
            "acs_user_id": "user",
            "acs_user_password": "user-pw",
            "acs_billing_code": "AK1",
            "acs_station_origin": "AT",
        },
        {
            "api_key": "key",
            "company_id": "company",
            "company_password": "company-pw",
            "user_id": "user",
            "user_password": "user-pw",
            "billing_code": "AK1",
            "station_origin": "AT",
        },
    ),
    "boxnow": (
        credentials.box_now_credentials,
        {
            "box_now_client_id": "cid",
            "box_now_client_secret": "csecret",
            "box_now_partner_id": "42",
            "box_now_warehouse_id": "wh",
            "box_now_notify_phone": "+302100000000",
            "box_now_webhook_secret": "whsecret",
        },
        {
            "client_id": "cid",
            "client_secret": "csecret",
            "partner_id": "42",
            "warehouse_id": "wh",
            "notify_phone": "+302100000000",
            "webhook_secret": "whsecret",
        },
    ),
    "mydata": (
        credentials.mydata_credentials,
        {"mydata_user_id": "aade-user", "mydata_subscription_key": "sub-key"},
        {"user_id": "aade-user", "subscription_key": "sub-key"},
    ),
}


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_credentials_resolve_under_schema_context(family):
    reader, fields, expected = FAMILIES[family]
    tenant = store_tenant(f"creds_{family}", **fields)

    with schema_context(tenant.schema_name):
        assert reader() == expected


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_a_missing_credential_still_reads_as_missing(family):
    reader, fields, _ = FAMILIES[family]
    configured = store_tenant(f"configured_{family}", **fields)
    bare = store_tenant(f"bare_{family}")

    with schema_context(bare.schema_name):
        values = reader()
    assert not any(values.values())

    # The configured store's values are not borrowed by the bare one.
    with schema_context(configured.schema_name):
        assert any(reader().values())


def test_meta_capi_and_pixel_resolve_under_schema_context():
    tenant = store_tenant(
        "creds_meta",
        meta_capi_access_token="token",
        meta_capi_dataset_id="dataset",
        meta_pixel_id="pixel",
    )

    with schema_context(tenant.schema_name):
        assert credentials.tenant_meta_capi_access_token() == "token"
        assert credentials.tenant_meta_capi_dataset_id() == "dataset"
        assert credentials.tenant_meta_pixel_id() == "pixel"


def test_non_credential_fields_resolve_under_schema_context():
    tenant = store_tenant(
        "creds_branding",
        store_name="Branded Store",
        contact_email="hello@example.com",
        gift_cards_enabled=False,
    )

    with schema_context(tenant.schema_name):
        assert credentials.tenant_site_name() == "Branded Store"
        assert credentials.tenant_contact_email() == "hello@example.com"
        assert tenant_plan_allows("gift_cards_enabled") is False


def test_a_schema_without_a_row_and_the_public_schema_are_no_store():
    with schema_context("no_such_store"):
        assert bound_tenant() is None
        assert not any(credentials.mydata_credentials().values())

    with schema_context("public"):
        assert bound_tenant() is None


def test_the_row_is_read_once_per_binding_and_not_across_bindings():
    tenant = store_tenant("creds_once", mydata_user_id="first")

    with schema_context(tenant.schema_name):
        with CaptureQueriesContext(connection) as captured:
            for _ in range(5):
                credentials.mydata_credentials()
        row_reads = [
            q["sql"] for q in captured if 'FROM "tenant_tenant"' in q["sql"]
        ]
        assert len(row_reads) == 1

    tenant.mydata_user_id = "second"
    tenant.save(update_fields=["mydata_user_id"])

    with schema_context(tenant.schema_name):
        assert credentials.mydata_credentials()["user_id"] == "second"


def test_a_bound_row_is_not_looked_up_again(django_assert_num_queries):
    tenant = store_tenant("creds_bound", mydata_user_id="bound")
    connection.set_tenant(tenant)
    try:
        with django_assert_num_queries(0):
            assert bound_tenant() is tenant
            assert credentials.mydata_credentials()["user_id"] == "bound"
    finally:
        connection.set_schema_to_public()


def test_shipped_receipt_is_dispatched_under_schema_context():
    """The production case: the commit hook re-enters the schema with
    ``schema_context`` and ``handle_order_shipped`` must still see the
    store's myDATA credentials."""
    tenant = store_tenant(
        "mydata_shipped",
        mydata_user_id="aade-user",
        mydata_subscription_key="sub-key",
    )
    order = OrderFactory(
        num_order_items=0,
        status=OrderStatus.SHIPPED,
        document_type=OrderDocumentTypeEnum.RECEIPT,
    )
    settings_by_key = {"MYDATA_ENABLED": True, "MYDATA_AUTO_SUBMIT": True}

    def fake_get(name, default=None):
        return settings_by_key.get(name, default)

    with (
        patch("order.mydata.config.Setting.get", side_effect=fake_get),
        patch("order.signals.handlers.dispatch_on_commit") as dispatch,
        schema_context(tenant.schema_name),
    ):
        handle_order_shipped(sender=type(order), order=order)

    dispatch.assert_called_once_with(generate_order_invoice, [order.id])


def test_shipped_receipt_is_not_dispatched_without_credentials():
    tenant = store_tenant("mydata_unconfigured")
    order = OrderFactory(
        num_order_items=0,
        status=OrderStatus.SHIPPED,
        document_type=OrderDocumentTypeEnum.RECEIPT,
    )
    settings_by_key = {"MYDATA_ENABLED": True, "MYDATA_AUTO_SUBMIT": True}

    with (
        patch(
            "order.mydata.config.Setting.get",
            side_effect=lambda name, default=None: settings_by_key.get(
                name, default
            ),
        ),
        patch("order.signals.handlers.dispatch_on_commit") as dispatch,
        schema_context(tenant.schema_name),
    ):
        handle_order_shipped(sender=type(order), order=order)

    dispatch.assert_not_called()
