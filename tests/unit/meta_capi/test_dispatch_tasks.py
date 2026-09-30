"""Dispatch, audit and retry behaviour of the Meta CAPI Celery tasks.

Covers ``meta_capi/tasks.py``: every ``dispatch_*`` task's gates (kill
switch, missing row, consent), ``_dispatch``'s SENT short-circuit and its
FAILED/SENT audit rows for transient, permanent and config errors, and the
``schedule_*`` helpers. ``MetaCapiClient`` — the facade over the Graph API
HTTP call — is the only thing mocked.
"""

from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from extra_settings.models import Setting
from facebook_business.adobjects.adspixel import AdsPixel
from facebook_business.exceptions import FacebookRequestError

from meta_capi.client import MetaCapiResponse
from meta_capi.exceptions import (
    MetaCapiConfigError,
    MetaCapiError,
    MetaCapiTransientError,
)
from meta_capi.models import MetaCapiEventLog, MetaCapiEventStatus
from meta_capi.tasks import (
    dispatch_complete_registration_event,
    dispatch_initiate_checkout_event,
    dispatch_purchase_event,
    dispatch_refund_event,
    schedule_initiate_checkout,
    schedule_purchase,
    schedule_refund,
)
from order.factories.order import OrderFactory
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

_CONSENTED_META = {
    "meta": {
        "consent": {"ads": True},
        "event_ids": {"purchase": "evt-purchase-1"},
        "client_ip_address": "203.0.113.9",
    }
}


def _order(**kwargs):
    # Faker's phone numbers can carry an extension ("...x305"), which the
    # Facebook SDK's normaliser rejects; a fixed E.164 number keeps the
    # payload, and so the run, deterministic.
    return OrderFactory(**{"phone": "+302101234567", **kwargs})


def _set_kill_switch(enabled: bool) -> None:
    Setting.objects.update_or_create(
        name="META_CAPI_ENABLED",
        defaults={"value": enabled, "value_type": "bool"},
    )


@pytest.fixture
def capi_enabled(bind_tenant):
    bind_tenant(
        SimpleNamespace(
            schema_name="test-capi-tenant",
            meta_pixel_id="1234567890",
            meta_capi_access_token="EAA-test-token",
        )
    )
    _set_kill_switch(True)


@pytest.fixture
def meta_send():
    with patch("meta_capi.tasks.MetaCapiClient") as client_cls:
        send = client_cls.return_value.send
        send.return_value = MetaCapiResponse(
            events_received=1, fbtrace_id="trace-abc", messages=[]
        )
        yield send


def _sent_event(send):
    (events,), _ = send.call_args
    assert len(events) == 1
    return events[0]


class TestPurchaseDispatch:
    def test_success_records_a_sent_audit_row(self, capi_enabled, meta_send):
        order = _order(metadata=_CONSENTED_META, num_order_items=1)

        dispatch_purchase_event.run(order.id)

        event = _sent_event(meta_send)
        assert event.event_id == "evt-purchase-1"
        assert event.event_name == "Purchase"
        row = MetaCapiEventLog.objects.get(event_id="evt-purchase-1")
        assert row.status == MetaCapiEventStatus.SENT
        assert row.event_name == "Purchase"
        assert row.order_id == order.id
        assert row.user_id == order.user_id
        assert row.fbtrace_id == "trace-abc"
        assert row.events_received == 1
        assert row.error_message == ""
        # The audit copy never holds the clear-text client IP.
        assert "client_ip_address" not in row.payload["user_data"]

    def test_already_sent_event_is_not_resent(self, capi_enabled, meta_send):
        order = _order(metadata=_CONSENTED_META, num_order_items=1)
        dispatch_purchase_event.run(order.id)
        meta_send.reset_mock()

        dispatch_purchase_event.run(order.id)

        meta_send.assert_not_called()
        assert (
            MetaCapiEventLog.objects.filter(event_id="evt-purchase-1").count()
            == 1
        )

    def test_transient_error_marks_failed_and_reraises(
        self, capi_enabled, meta_send
    ):
        order = _order(metadata=_CONSENTED_META, num_order_items=1)
        meta_send.side_effect = MetaCapiTransientError("Meta CAPI 503: down")

        with pytest.raises(MetaCapiTransientError):
            dispatch_purchase_event.run(order.id)

        row = MetaCapiEventLog.objects.get(event_id="evt-purchase-1")
        assert row.status == MetaCapiEventStatus.FAILED
        assert row.error_message == "Meta CAPI 503: down"

    def test_retry_after_transient_failure_flips_the_row_to_sent(
        self, capi_enabled, meta_send
    ):
        order = _order(metadata=_CONSENTED_META, num_order_items=1)
        meta_send.side_effect = MetaCapiTransientError("timeout")
        with pytest.raises(MetaCapiTransientError):
            dispatch_purchase_event.run(order.id)
        meta_send.side_effect = None

        dispatch_purchase_event.run(order.id)

        row = MetaCapiEventLog.objects.get(event_id="evt-purchase-1")
        assert row.status == MetaCapiEventStatus.SENT
        assert row.error_message == ""

    @pytest.mark.parametrize(
        "error",
        [
            MetaCapiError("Meta CAPI 400: invalid pixel"),
            MetaCapiConfigError("Tenant.meta_capi_access_token is not set"),
        ],
        ids=["permanent", "config"],
    )
    def test_permanent_error_marks_failed_without_raising(
        self, capi_enabled, meta_send, error
    ):
        order = _order(metadata=_CONSENTED_META, num_order_items=1)
        meta_send.side_effect = error

        dispatch_purchase_event.run(order.id)

        row = MetaCapiEventLog.objects.get(event_id="evt-purchase-1")
        assert row.status == MetaCapiEventStatus.FAILED
        assert row.error_message == str(error)

    def test_error_message_is_truncated_to_1000_chars(
        self, capi_enabled, meta_send
    ):
        order = _order(metadata=_CONSENTED_META, num_order_items=1)
        meta_send.side_effect = MetaCapiError("x" * 1500)

        dispatch_purchase_event.run(order.id)

        row = MetaCapiEventLog.objects.get(event_id="evt-purchase-1")
        assert row.error_message == "x" * 1000

    def test_order_without_ad_consent_is_not_sent(
        self, capi_enabled, meta_send
    ):
        order = _order(
            metadata={"meta": {"consent": {"ads": False}}}, num_order_items=1
        )

        dispatch_purchase_event.run(order.id)

        meta_send.assert_not_called()
        assert not MetaCapiEventLog.objects.filter(order=order).exists()

    def test_kill_switch_off_sends_nothing(self, capi_enabled, meta_send):
        _set_kill_switch(False)
        order = _order(metadata=_CONSENTED_META, num_order_items=1)

        dispatch_purchase_event.run(order.id)

        meta_send.assert_not_called()
        assert not MetaCapiEventLog.objects.filter(order=order).exists()

    def test_tenant_without_credentials_sends_nothing(
        self, bind_tenant, meta_send
    ):
        bind_tenant(
            SimpleNamespace(
                schema_name="test-capi-unconfigured",
                meta_pixel_id="",
                meta_capi_access_token="",
            )
        )
        _set_kill_switch(True)
        order = _order(metadata=_CONSENTED_META, num_order_items=1)

        dispatch_purchase_event.run(order.id)

        meta_send.assert_not_called()

    def test_vanished_order_is_ignored(self, capi_enabled, meta_send):
        dispatch_purchase_event.run(987654321)

        meta_send.assert_not_called()
        assert not MetaCapiEventLog.objects.exists()


class TestInitiateCheckoutDispatch:
    def test_sends_initiate_checkout_with_the_browser_event_id(
        self, capi_enabled, meta_send
    ):
        metadata = {
            "meta": {
                "consent": {"ads": True},
                "event_ids": {"initiate_checkout": "evt-ic-9"},
            }
        }
        order = _order(metadata=metadata, num_order_items=1)

        dispatch_initiate_checkout_event.run(order.id)

        event = _sent_event(meta_send)
        assert event.event_name == "InitiateCheckout"
        row = MetaCapiEventLog.objects.get(event_id="evt-ic-9")
        assert row.event_name == "InitiateCheckout"
        assert row.status == MetaCapiEventStatus.SENT

    def test_no_consent_and_missing_order_send_nothing(
        self, capi_enabled, meta_send
    ):
        order = _order(metadata={}, num_order_items=1)

        dispatch_initiate_checkout_event.run(order.id)
        dispatch_initiate_checkout_event.run(987654321)

        meta_send.assert_not_called()


class TestRefundDispatch:
    def test_explicit_amount_is_reported(self, capi_enabled, meta_send):
        order = _order(metadata=_CONSENTED_META, num_order_items=1)

        dispatch_refund_event.run(order.id, "12.50")

        event = _sent_event(meta_send)
        assert event.event_name == "Refund"
        assert event.custom_data.value == 12.5
        assert event.custom_data.order_id == str(order.id)
        row = MetaCapiEventLog.objects.get(event_id=event.event_id)
        assert row.event_name == "Refund"
        assert row.status == MetaCapiEventStatus.SENT

    def test_missing_amount_falls_back_to_paid_amount(
        self, capi_enabled, meta_send
    ):
        order = _order(metadata=_CONSENTED_META, num_order_items=1)
        order.refresh_from_db()

        dispatch_refund_event.run(order.id)

        event = _sent_event(meta_send)
        assert event.custom_data.value == float(order.paid_amount.amount)
        assert event.custom_data.currency == order.paid_amount.currency.code

    def test_refund_without_consent_is_not_sent(self, capi_enabled, meta_send):
        order = _order(metadata={}, num_order_items=1)

        dispatch_refund_event.run(order.id, "5.00")

        meta_send.assert_not_called()


class TestCompleteRegistrationDispatch:
    def test_sends_with_supplied_event_id_and_no_order(
        self, capi_enabled, meta_send
    ):
        user = UserAccountFactory()

        dispatch_complete_registration_event.run(
            user.id,
            fbp="fb.1.123",
            event_id="evt-reg-1",
            event_source_url="https://shop.example/register",
        )

        event = _sent_event(meta_send)
        assert event.event_name == "CompleteRegistration"
        assert event.event_source_url == "https://shop.example/register"
        row = MetaCapiEventLog.objects.get(event_id="evt-reg-1")
        assert row.user_id == user.id
        assert row.order_id is None
        assert row.status == MetaCapiEventStatus.SENT
        assert "fbp" not in row.payload["user_data"]

    def test_unknown_user_and_disabled_capi_send_nothing(
        self, capi_enabled, meta_send
    ):
        dispatch_complete_registration_event.run(987654321)
        _set_kill_switch(False)
        dispatch_complete_registration_event.run(UserAccountFactory().id)

        meta_send.assert_not_called()


class TestScheduleHelpers:
    def test_schedule_refund_passes_the_amount_as_a_string(self):
        with patch("meta_capi.tasks.dispatch_on_commit") as dispatch:
            schedule_refund(5, Decimal("12.50"))
            schedule_refund(6, None)

        assert [c.args for c in dispatch.call_args_list] == [
            (dispatch_refund_event, [5, "12.50"]),
            (dispatch_refund_event, [6, None]),
        ]

    def test_schedule_purchase_and_initiate_checkout_target_their_tasks(
        self,
    ):
        with patch("meta_capi.tasks.dispatch_on_commit") as dispatch:
            schedule_purchase(7)
            schedule_initiate_checkout(8)

        assert [c.args for c in dispatch.call_args_list] == [
            (dispatch_purchase_event, [7]),
            (dispatch_initiate_checkout_event, [8]),
        ]


class TestSdkRequestErrors:
    """A genuine ``FacebookRequestError`` from the Graph API POST, with
    only ``AdsPixel.create_event`` patched, reaches the audit row."""

    @staticmethod
    def _graph_error(status: int, error: dict):
        return patch.object(
            AdsPixel,
            "create_event",
            side_effect=FacebookRequestError(
                message="Call was not successful",
                request_context={"method": "POST", "path": "/events"},
                http_status=status,
                http_headers={},
                body=json.dumps({"error": error}),
            ),
        )

    def test_503_marks_failed_and_reraises_for_retry(self, capi_enabled):
        # The real SDK normalises the phone; a Faker number with an
        # extension would fail before the POST is reached.
        order = _order(
            metadata=_CONSENTED_META, num_order_items=1, phone="+302101234567"
        )

        with (
            self._graph_error(
                503, {"message": "Temporarily down", "fbtrace_id": "T503"}
            ),
            pytest.raises(MetaCapiTransientError),
        ):
            dispatch_purchase_event.run(order.id)

        row = MetaCapiEventLog.objects.get(event_id="evt-purchase-1")
        assert row.status == MetaCapiEventStatus.FAILED
        assert row.error_message == (
            "Meta CAPI 503: Temporarily down (fbtrace=T503)"
        )

    def test_400_marks_failed_without_raising(self, capi_enabled):
        # The real SDK normalises the phone; a Faker number with an
        # extension would fail before the POST is reached.
        order = _order(
            metadata=_CONSENTED_META, num_order_items=1, phone="+302101234567"
        )

        with self._graph_error(
            400, {"message": "Invalid parameter", "fbtrace_id": "T400"}
        ):
            dispatch_purchase_event.run(order.id)

        row = MetaCapiEventLog.objects.get(event_id="evt-purchase-1")
        assert row.status == MetaCapiEventStatus.FAILED
        assert row.error_message == (
            "Meta CAPI 400: Invalid parameter (fbtrace=T400)"
        )
