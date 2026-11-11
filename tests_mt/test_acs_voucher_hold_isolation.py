"""One store's ACS voucher hold never holds another store's requests.

ACS credentials are per store (``tenant.credentials.acs_credentials``),
so an account that fails says nothing about another store's account.
``AcsVoucherHold`` and the shipments' ``voucher_request_held_at`` live in
each store's own schema; the main suite has no tenant schemas, so the
scope is proven here against two real ones, with ACS as the HTTP session.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import requests
from django_tenants.utils import tenant_context

pytestmark = pytest.mark.django_db

CREATE = "ACS_Create_Voucher"


def _response(status_code: int, body: dict | None = None):
    response = MagicMock(spec=requests.Response)
    response.status_code = status_code
    response.text = json.dumps(body) if body is not None else "Bad Gateway"
    response.json.return_value = body or {}
    return response


class _Acs:
    """ACS behind the HTTP session, failing for one ``Company_ID`` only."""

    def __init__(self, failing_company: str) -> None:
        self.failing_company = failing_company
        self.creates: list[str] = []

    def post(self, url, *, json, headers, timeout):
        assert json["ACSAlias"] == CREATE
        company = json["ACSInputParameters"]["Company_ID"]
        self.creates.append(company)
        if company == self.failing_company:
            return _response(502)
        return _response(
            200,
            {
                "ACSExecution_HasError": False,
                "ACSOutputResponce": {
                    "ACSValueOutput": [{"Voucher_No": "7401573174"}]
                },
            },
        )


@pytest.fixture
def two_stores(mt_tenant, mt_tenant_b):
    yield mt_tenant, mt_tenant_b
    from order.models.order import Order
    from shipping_acs.models import AcsShipment, AcsVoucherHold

    for tenant in (mt_tenant, mt_tenant_b):
        with tenant_context(tenant):
            AcsVoucherHold.objects.all().delete()
            AcsShipment.objects.all().delete()
            Order.objects.all().delete()


def _client_of(remote: _Acs, company: str):
    from shipping_acs.client import AcsClient

    def client() -> AcsClient:
        return AcsClient(
            api_key="TEST_API_KEY",
            company_id=company,
            company_password="TEST_PASS",
            user_id="TEST_USER",
            user_password="TEST_USER_PASS",
            billing_code="TEST_BILLING",
            api_base_url="https://example/acs",
            session=remote,
        )

    return client


def _request_a_voucher(tenant, remote: _Acs) -> dict:
    """A cash-on-delivery order of ``tenant`` asks ACS for its voucher."""
    from order.enum.status import OrderStatus
    from shipping_acs.enum.shipment_state import AcsShipmentState
    from shipping_acs.factories import AcsShipmentFactory
    from shipping_acs.tasks import create_acs_voucher_for_order
    from tests.utils.orders import courier_cash_order

    with (
        tenant_context(tenant),
        patch(
            "shipping_acs.services.AcsClient",
            _client_of(remote, tenant.schema_name),
        ),
        patch("shipping_acs.services.send_email"),
    ):
        shipment = AcsShipmentFactory(
            order=courier_cash_order(status=OrderStatus.PENDING),
            voucher_no=None,
            shipment_state=AcsShipmentState.PENDING_CREATION,
        )
        return create_acs_voucher_for_order.run(shipment.order_id)


def test_a_hold_in_one_store_does_not_hold_the_other(two_stores):
    from shipping_acs import attention, voucher_hold

    failing, healthy = two_stores
    remote = _Acs(failing_company=failing.schema_name)

    lost = _request_a_voucher(failing, remote)
    held = _request_a_voucher(failing, remote)
    sent = _request_a_voucher(healthy, remote)

    assert lost["status"] == "voucher_request_unknown"
    assert held["status"] == "voucher_request_held"
    assert sent["status"] == "ok"
    # The failing store sent one request, the lost one; the other store's
    # request went out although that hold was in force.
    assert remote.creates == [failing.schema_name, healthy.schema_name]
    with tenant_context(failing):
        assert voucher_hold.in_force() is not None
        assert attention.voucher_requests_held().count() == 1
    with tenant_context(healthy):
        assert voucher_hold.in_force() is None
        assert attention.voucher_requests_held().count() == 0


def test_the_sweep_of_one_store_sends_only_its_own_held_requests(two_stores):
    from shipping_acs import attention, voucher_hold
    from shipping_acs.tasks import issue_held_acs_vouchers

    first, second = two_stores
    remote = _Acs(failing_company="nobody")
    for tenant in two_stores:
        with tenant_context(tenant):
            voucher_hold.record_unknown_outcome(
                "ACS server error 502", "0:2026-11-11T10:00:00+00:00"
            )
        assert _request_a_voucher(tenant, remote)["status"] == (
            "voucher_request_held"
        )
    assert remote.creates == []

    with (
        tenant_context(first),
        patch("shipping_acs.config.is_configured", return_value=True),
        patch(
            "shipping_acs.services.AcsClient",
            _client_of(remote, first.schema_name),
        ),
        patch("shipping_acs.services.send_email"),
    ):
        assert voucher_hold.allow_trial()
        result = issue_held_acs_vouchers.run()

    assert result == {"status": "one_request_first", "dispatched": 1}
    assert remote.creates == [first.schema_name]
    with tenant_context(first):
        assert voucher_hold.in_force() is None
        assert attention.voucher_requests_held().count() == 0
    with tenant_context(second):
        assert voucher_hold.in_force() is not None
        assert attention.voucher_requests_held().count() == 1
