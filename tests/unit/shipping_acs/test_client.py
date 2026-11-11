"""Unit tests for AcsClient — all HTTP interactions are mocked."""

from __future__ import annotations

import base64
import json
from http.client import RemoteDisconnected
from unittest.mock import MagicMock

import pytest
import requests
from urllib3.exceptions import (
    MaxRetryError,
    NameResolutionError,
    NewConnectionError,
    ProtocolError,
    SSLError,
)

from shipping_acs.client import _RETRY_CONFIG, AcsClient
from shipping_acs.exceptions import (
    AcsAPIError,
    AcsAuthError,
    AcsConfigError,
    AcsOutcomeUnknownError,
    AcsRetryableError,
)

# A voucher request asks the store's voucher hold, a row, whether it may
# be sent (``AcsClient._admit_voucher_request``, #631).
pytestmark = pytest.mark.django_db


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

        with (
            override_settings(
                ACS_API_KEY="",
                ACS_COMPANY_ID="",
                ACS_COMPANY_PASSWORD="",
                ACS_USER_ID="",
                ACS_USER_PASSWORD="",
            ),
            pytest.raises(AcsConfigError),
        ):
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


class TestRetryPolicy:
    def test_a_post_is_not_sent_again_after_an_error_of_unknown_timing(self):
        # Neither a connect nor a read error: urllib3 retries it for any
        # method unless ``other`` is 0, and the voucher may have been made.
        with pytest.raises(MaxRetryError):
            _RETRY_CONFIG.increment(method="POST", error=SSLError("tls"))

    def test_a_post_is_sent_again_when_the_connection_was_refused(self):
        after = _RETRY_CONFIG.increment(
            method="POST", error=NewConnectionError(None, "refused")
        )

        assert after.total == 2


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

    def test_auth_error_is_retryable(self):
        """Prod ACS returns sporadic transient 406s (same key/IP
        succeeds for sibling calls in the same second — verified
        2026-07-11), so auth errors must autoretry instead of
        permanently failing e.g. a voucher mint."""
        client = _make_client()
        client._session.post.return_value = _make_response(406)
        with pytest.raises(AcsRetryableError):
            client._call("ACS_Trackingsummary")

    def test_other_4xx_raises_api_error(self):
        client = _make_client()
        client._session.post.return_value = _make_response(404)
        with pytest.raises(AcsAPIError):
            client._call("ACS_Create_Voucher")


class TestOutcomeOfAFailedCall:
    """Whether a failed call may have been executed at ACS.

    The manual (April 2026, p. 1-2) says the server always answers 200,
    403 or 406; it says nothing of a 5xx, a lost answer or a timeout. So
    only a connection that was never established proves the request
    never reached ACS. Every other failure after the request may have
    left is an unknown outcome: retryable for a read, never re-sent for
    ``ACS_Create_Voucher`` (``AcsService.create_voucher_for_order``).
    """

    @staticmethod
    def _raised(side_effect=None, response=None) -> AcsRetryableError:
        client = _make_client()
        if response is not None:
            client._session.post.return_value = response
        else:
            client._session.post.side_effect = side_effect
        with pytest.raises(AcsRetryableError) as exc_info:
            client._call("ACS_Create_Voucher")
        return exc_info.value

    def test_a_read_timeout_is_an_unknown_outcome(self):
        """Wrapped, never raw (prod 2026-07-04: a 15 s read timeout
        surfaced as an unhandled 500 on address validation)."""
        exc = self._raised(requests.ReadTimeout("slow ACS"))

        assert isinstance(exc, AcsOutcomeUnknownError)

    def test_a_connection_dropped_after_the_request_is_an_unknown_outcome(
        self,
    ):
        # requests re-raises urllib3's ProtocolError ("the server began
        # processing it") as a plain ConnectionError.
        exc = self._raised(
            requests.ConnectionError(
                ProtocolError("Connection aborted.", RemoteDisconnected())
            )
        )

        assert isinstance(exc, AcsOutcomeUnknownError)

    def test_a_connection_error_of_unknown_cause_is_an_unknown_outcome(self):
        exc = self._raised(requests.ConnectionError("boom"))

        assert isinstance(exc, AcsOutcomeUnknownError)

    def test_a_server_error_is_an_unknown_outcome(self):
        exc = self._raised(response=_make_response(503))

        assert isinstance(exc, AcsOutcomeUnknownError)
        assert exc.http_status == 503

    def test_a_200_whose_body_is_not_json_is_an_unknown_outcome(self):
        response = _make_response(200)
        response.json.side_effect = ValueError("Expecting value")

        exc = self._raised(response=response)

        assert isinstance(exc, AcsOutcomeUnknownError)

    def test_a_connect_timeout_never_reached_acs(self):
        exc = self._raised(requests.ConnectTimeout("no route"))

        assert not isinstance(exc, AcsOutcomeUnknownError)

    @pytest.mark.parametrize(
        "reason",
        [
            NewConnectionError(None, "refused"),
            NameResolutionError("acs.example", None, OSError("no DNS")),
        ],
    )
    def test_a_connection_never_established_never_reached_acs(self, reason):
        exc = self._raised(
            requests.ConnectionError(MaxRetryError(None, "/", reason))
        )

        assert not isinstance(exc, AcsOutcomeUnknownError)

    def test_an_auth_layer_rejection_is_retryable_not_unknown(self):
        exc = self._raised(response=_make_response(406))

        assert isinstance(exc, AcsAuthError)
        assert not isinstance(exc, AcsOutcomeUnknownError)

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
            {"Recipient_Name": "TEST", "Weight": "1.0"},
            request_id="1:2026-11-11T10:00:00+00:00",
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
        assert result["Unprinted_Vouchers"] == []

    def test_issue_pickup_list_lifts_unprinted_vouchers_from_table(self):
        # ACS puts the refusal count in the value row but the offending
        # voucher NUMBERS only in ACSTableOutput (manual: "Unprinted
        # vouchers" response). Returning the value row alone discarded
        # the one field that says what to go and print.
        client = _make_client()
        client._session.post.return_value = _make_response(
            200,
            {
                "ACSExecution_HasError": False,
                "ACSOutputResponce": {
                    "ACSValueOutput": [
                        {
                            "PickupList_No": None,
                            "Unprinted_Found": 2,
                            "Error_Message": "Unprinted vouchers found.",
                        }
                    ],
                    "ACSTableOutput": {
                        "Table_Data": [
                            {"Unprinted_Vouchers": "7227889841"},
                            {"Unprinted_Vouchers": "7227889874"},
                        ]
                    },
                },
            },
        )

        result = client.issue_pickup_list(pickup_date="2026-04-29")

        assert result["PickupList_No"] is None
        assert result["Unprinted_Found"] == 2
        assert result["Unprinted_Vouchers"] == ["7227889841", "7227889874"]


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
