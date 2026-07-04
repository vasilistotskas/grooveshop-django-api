"""Unit tests for AcsClient — all HTTP interactions are mocked."""

from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock

import pytest
import requests

from shipping_acs.client import AcsClient
from shipping_acs.exceptions import (
    AcsAPIError,
    AcsAuthError,
    AcsConfigError,
    AcsRetryableError,
)


def _make_response(status_code: int, body=None):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.ok = status_code < 400
    if body is None:
        resp.text = ""
        resp.json.return_value = {}
    else:
        resp.text = json.dumps(body)
        resp.json.return_value = body
    return resp


def _make_client(**overrides) -> AcsClient:
    defaults = {
        "api_key": "TEST_API_KEY",
        "company_id": "TEST_COMPANY",
        "company_password": "TEST_PASS",
        "user_id": "TEST_USER",
        "user_password": "TEST_USER_PASS",
        "billing_code": "2ΑΚ89587",
        "api_base_url": "https://example/acs",
        "session": MagicMock(),
    }
    defaults.update(overrides)
    return AcsClient(**defaults)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class TestConfig:
    def test_missing_credentials_raise_config_error(self):
        """With no settings fallback, an empty kwarg must crash early.

        Overrides settings to empty so the ``or getattr(settings, ...)``
        fallback inside ``AcsClient.__init__`` can't paper over the
        missing kwargs.  Without this override the test was depending
        on ``.env`` being empty — which silently broke the moment we
        populated stage credentials in dev.
        """
        from django.test import override_settings

        with override_settings(
            ACS_API_KEY="",
            ACS_COMPANY_ID="",
            ACS_COMPANY_PASSWORD="",
            ACS_USER_ID="",
            ACS_USER_PASSWORD="",
        ):
            with pytest.raises(AcsConfigError):
                AcsClient(
                    api_key="",
                    company_id="x",
                    company_password="x",
                    user_id="x",
                    user_password="x",
                )


# ---------------------------------------------------------------------------
# _call envelope handling
# ---------------------------------------------------------------------------


class TestCall:
    def test_strips_misspelled_envelope(self):
        client = _make_client()
        client._session.post.return_value = _make_response(
            200,
            {
                "ACSExecution_HasError": False,
                "ACSExecutionErrorMessage": "",
                "ACSOutputResponce": {  # wire misspelling
                    "ACSValueOutput": [{"Voucher_No": "7227890000"}],
                    "ACSTableOutput": {},
                },
            },
        )

        result = client._call("ACS_Create_Voucher", {"Pickup_Date": "x"})
        assert result["ACSValueOutput"][0]["Voucher_No"] == "7227890000"

    def test_accepts_corrected_envelope_spelling(self):
        # Defensive: tolerate a future ACS fix that removes the typo.
        client = _make_client()
        client._session.post.return_value = _make_response(
            200,
            {
                "ACSExecution_HasError": False,
                "ACSOutputResponse": {  # corrected spelling
                    "ACSValueOutput": [{"hello": "world"}],
                },
            },
        )
        result = client._call("ACS_TestAlias", None)
        assert result["ACSValueOutput"][0]["hello"] == "world"

    def test_raises_when_has_error_flag_true(self):
        client = _make_client()
        client._session.post.return_value = _make_response(
            200,
            {
                "ACSExecution_HasError": True,
                "ACSExecutionErrorMessage": "Voucher already exists.",
            },
        )

        with pytest.raises(AcsAPIError) as exc_info:
            client._call("ACS_Create_Voucher", {})
        assert "Voucher already exists" in str(exc_info.value)
        assert exc_info.value.alias == "ACS_Create_Voucher"

    def test_403_raises_auth_error(self):
        client = _make_client()
        client._session.post.return_value = _make_response(403)
        with pytest.raises(AcsAuthError):
            client._call("ACS_Create_Voucher")

    def test_500_raises_retryable_error(self):
        client = _make_client()
        client._session.post.return_value = _make_response(503)
        with pytest.raises(AcsRetryableError):
            client._call("ACS_Create_Voucher")

    def test_other_4xx_raises_api_error(self):
        client = _make_client()
        client._session.post.return_value = _make_response(404)
        with pytest.raises(AcsAPIError):
            client._call("ACS_Create_Voucher")

    def test_connection_error_raises_retryable(self):
        client = _make_client()
        client._session.post.side_effect = requests.ConnectionError("boom")
        with pytest.raises(AcsRetryableError):
            client._call("ACS_Create_Voucher")

    def test_read_timeout_raises_retryable(self):
        """ReadTimeout is not a ConnectionError subclass — it must be
        wrapped explicitly or it escapes raw (prod 2026-07-04: 15s
        ACS read timeout surfaced as an unhandled 500 on the
        address-validation checkout endpoint)."""
        client = _make_client()
        client._session.post.side_effect = requests.ReadTimeout("slow ACS")
        with pytest.raises(AcsRetryableError):
            client._call("ACS_Address_Validation")

    def test_connect_timeout_raises_retryable(self):
        client = _make_client()
        client._session.post.side_effect = requests.ConnectTimeout("no route")
        with pytest.raises(AcsRetryableError):
            client._call("ACS_Address_Validation")

    def test_payload_includes_creds_and_acs_alias(self):
        client = _make_client()
        client._session.post.return_value = _make_response(
            200,
            {
                "ACSExecution_HasError": False,
                "ACSOutputResponce": {"ACSValueOutput": [{}]},
            },
        )

        client._call("ACS_Test", {"Pickup_Date": "2025-01-01"})

        call_kwargs = client._session.post.call_args.kwargs
        assert call_kwargs["headers"]["AcsApiKey"] == "TEST_API_KEY"
        body = call_kwargs["json"]
        assert body["ACSAlias"] == "ACS_Test"
        params = body["ACSInputParameters"]
        assert params["Company_ID"] == "TEST_COMPANY"
        assert params["User_Password"] == "TEST_USER_PASS"
        assert params["Pickup_Date"] == "2025-01-01"


# ---------------------------------------------------------------------------
# Voucher creation + multipart
# ---------------------------------------------------------------------------


class TestVoucher:
    def test_create_voucher_returns_first_value_output_row(self):
        client = _make_client()
        client._session.post.return_value = _make_response(
            200,
            {
                "ACSExecution_HasError": False,
                "ACSOutputResponce": {
                    "ACSValueOutput": [
                        {"Voucher_No": "7227891234", "Error_Message": ""}
                    ],
                    "ACSTableOutput": {},
                },
            },
        )

        result = client.create_voucher(
            {"Recipient_Name": "TEST", "Weight": "1.0"}
        )
        assert result["Voucher_No"] == "7227891234"

    def test_get_multipart_returns_child_voucher_numbers(self):
        client = _make_client()
        client._session.post.return_value = _make_response(
            200,
            {
                "ACSExecution_HasError": False,
                "ACSOutputResponce": {
                    "ACSValueOutput": [{}],
                    "ACSTableOutput": {
                        "Table_Data": [
                            {"MultiPart_Voucher_No": "8052453001"},
                            {"MultiPart_Voucher_No": "8052453002"},
                        ]
                    },
                },
            },
        )

        children = client.get_multipart_vouchers("7227891234")
        assert children == ["8052453001", "8052453002"]

    def test_print_voucher_decodes_base64_pdf(self):
        # ACS returns ACSObjectOutput=[{<voucher_no>: <base64 pdf>}]
        pdf_bytes = b"%PDF-1.7 fake bytes"
        encoded = base64.b64encode(pdf_bytes).decode("ascii")
        client = _make_client()
        client._session.post.return_value = _make_response(
            200,
            {
                "ACSExecution_HasError": False,
                "ACSOutputResponce": {
                    "ACSObjectOutput": [{"7227891234": encoded}],
                },
            },
        )

        result = client.print_voucher("7227891234")
        assert result == pdf_bytes


# ---------------------------------------------------------------------------
# Pickup list
# ---------------------------------------------------------------------------


class TestPickupList:
    def test_issue_pickup_list_returns_first_value_output(self):
        client = _make_client()
        client._session.post.return_value = _make_response(
            200,
            {
                "ACSExecution_HasError": False,
                "ACSOutputResponce": {
                    "ACSValueOutput": [
                        {
                            "PickupList_No": "7227889830",
                            "Unprinted_Found": 0,
                            "Error_Message": "",
                        }
                    ],
                    "ACSTableOutput": {"Table_Data": []},
                },
            },
        )

        result = client.issue_pickup_list(pickup_date="2026-04-29")
        assert result["PickupList_No"] == "7227889830"


# ---------------------------------------------------------------------------
# Tracking
# ---------------------------------------------------------------------------


class TestTracking:
    def test_tracking_summary_returns_first_table_row(self):
        client = _make_client()
        client._session.post.return_value = _make_response(
            200,
            {
                "ACSExecution_HasError": False,
                "ACSOutputResponce": {
                    "ACSValueOutput": [{"Error_Message": None}],
                    "ACSTableOutput": {
                        "Table_Data": [
                            {
                                "voucher_no": "7227891234",
                                "delivery_flag": 1,
                                "shipment_status": 5,
                            }
                        ]
                    },
                },
            },
        )

        snap = client.tracking_summary("7227891234")
        assert snap["delivery_flag"] == 1

    def test_tracking_details_returns_table_data(self):
        client = _make_client()
        client._session.post.return_value = _make_response(
            200,
            {
                "ACSExecution_HasError": False,
                "ACSOutputResponce": {
                    "ACSValueOutput": [{"Error_Message": ""}],
                    "ACSTableOutput": {
                        "Table_Data": [
                            {
                                "checkpoint_date_time": "2025-09-13T14:39:15.41",
                                "checkpoint_action": "ΠΑΡΑΛΑΒΗ",
                            },
                            {
                                "checkpoint_date_time": "2025-09-13T23:42:23.08",
                                "checkpoint_action": "ΦΟΡΤΩΣΗ",
                            },
                        ]
                    },
                },
            },
        )

        rows = client.tracking_details("7227891234")
        assert len(rows) == 2
        assert rows[0]["checkpoint_action"] == "ΠΑΡΑΛΑΒΗ"
