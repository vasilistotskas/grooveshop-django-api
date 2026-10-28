"""Every ACS call is logged once and written to ``AcsApiCall``.

``AcsClient._call`` is the chokepoint, so these drive the client the way
``test_client.py`` does (HTTP mocked) and read back the log record and the
history row. Credentials must reach neither.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.test import override_settings
from django.utils import timezone

from shipping_acs import observability
from shipping_acs.client import AcsClient
from shipping_acs.exceptions import (
    AcsAPIError,
    AcsAuthError,
    AcsRetryableError,
)
from shipping_acs.models import AcsApiCall
from shipping_acs.models.api_call import AcsApiCallOutcome

pytestmark = pytest.mark.django_db

CREDENTIALS = {
    "company_id": "SECRET_COMPANY_ID",
    "company_password": "SECRET_COMPANY_PASS",
    "user_id": "SECRET_USER_ID",
    "user_password": "SECRET_USER_PASS",
    "api_key": "SECRET_API_KEY",
}


def _response(status_code: int, body=None):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.ok = status_code < 400
    resp.text = json.dumps(body) if body is not None else ""
    resp.json.return_value = body if body is not None else {}
    return resp


def _client() -> AcsClient:
    return AcsClient(
        billing_code="BILL",
        api_base_url="https://example/acs",
        session=MagicMock(),
        **CREDENTIALS,
    )


def _ok(value_row=None):
    return {
        "ACSExecution_HasError": False,
        "ACSOutputResponce": {"ACSValueOutput": [value_row or {}]},
    }


def _log_records(caplog):
    return [
        r
        for r in caplog.records
        if r.name == "shipping_acs.client" and hasattr(r, "fields")
    ]


class TestLogLine:
    def test_one_line_with_the_queryable_fields(self, caplog):
        client = _client()
        client._session.post.return_value = _response(200, _ok())

        with caplog.at_level(logging.INFO, logger="shipping_acs.client"):
            client._call(
                "ACS_Print_Voucher", {"Voucher_No": "123", "Print_Type": 2}
            )

        [record] = _log_records(caplog)
        assert record.levelno == logging.INFO
        assert record.fields["acs_alias"] == "ACS_Print_Voucher"
        assert record.fields["outcome"] == "ok"
        assert record.fields["http_status"] == 200
        assert record.fields["voucher_no"] == "123"
        assert isinstance(record.fields["duration_ms"], int)
        assert record.fields["error_message"] == ""

    def test_json_formatter_carries_the_fields_and_the_schema(self, caplog):
        from core.logging import JsonFormatter

        client = _client()
        client._session.post.return_value = _response(
            200, _ok({"Error_Message": "no"})
        )
        with caplog.at_level(logging.INFO, logger="shipping_acs.client"):
            client._call("ACS_Issue_Pickup_List", {"Pickup_Date": "2026-10-28"})

        [record] = _log_records(caplog)
        payload = json.loads(JsonFormatter().format(record))
        assert payload["acs_alias"] == "ACS_Issue_Pickup_List"
        assert payload["pickup_date"] == "2026-10-28"
        assert "schema" in payload

    @pytest.mark.parametrize(
        ("setup", "exception", "outcome", "level"),
        [
            (
                lambda c: setattr(
                    c._session.post,
                    "return_value",
                    _response(
                        200,
                        {
                            "ACSExecution_HasError": True,
                            "ACSExecutionErrorMessage": "Voucher exists",
                        },
                    ),
                ),
                AcsAPIError,
                "business_error",
                logging.WARNING,
            ),
            (
                lambda c: setattr(
                    c._session.post, "return_value", _response(406)
                ),
                AcsAuthError,
                "auth",
                logging.WARNING,
            ),
            (
                lambda c: setattr(
                    c._session.post, "return_value", _response(503)
                ),
                AcsRetryableError,
                "retryable",
                logging.WARNING,
            ),
            (
                lambda c: setattr(
                    c._session.post,
                    "side_effect",
                    requests.ConnectionError("boom"),
                ),
                AcsRetryableError,
                "transport",
                logging.WARNING,
            ),
            (
                lambda c: setattr(
                    c._session.post, "return_value", _response(404)
                ),
                AcsAPIError,
                "unexpected",
                logging.ERROR,
            ),
        ],
    )
    def test_outcome_and_level(self, caplog, setup, exception, outcome, level):
        client = _client()
        setup(client)

        with (
            caplog.at_level(logging.INFO, logger="shipping_acs.client"),
            pytest.raises(exception),
        ):
            client._call("ACS_Create_Voucher", {})

        [record] = _log_records(caplog)
        assert record.fields["outcome"] == outcome
        assert record.levelno == level
        assert AcsApiCall.objects.get().outcome == outcome

    def test_a_refusal_in_the_value_row_is_a_warning_with_the_message(
        self, caplog
    ):
        client = _client()
        client._session.post.return_value = _response(
            200, _ok({"PickupList_No": None, "Error_Message": "Unprinted"})
        )

        with caplog.at_level(logging.INFO, logger="shipping_acs.client"):
            client._call("ACS_Issue_Pickup_List", {"Pickup_Date": "2026-10-22"})

        [record] = _log_records(caplog)
        assert record.levelno == logging.WARNING
        assert record.fields["outcome"] == "business_error"
        assert record.fields["error_message"] == "Unprinted"

    def test_identifiers_come_from_the_params(self, caplog):
        client = _client()
        client._session.post.return_value = _response(200, _ok())
        with caplog.at_level(logging.INFO, logger="shipping_acs.client"):
            client._call(
                "ACS_Create_Voucher",
                {"Reference_Key1": "448", "reference_no": "REF1"},
            )
        [record] = _log_records(caplog)
        assert record.fields["order_id"] == 448
        assert record.fields["reference_no"] == "REF1"


class TestRedaction:
    def test_redact_strips_credentials_at_any_depth(self):
        cleaned = observability.redact(
            {
                "Company_ID": "a",
                "nested": [{"User-Password": "b", "AcsApiKey": "c"}],
                "Voucher_No": "123",
            }
        )
        assert cleaned == {
            "Company_ID": observability.REDACTED,
            "nested": [
                {
                    "User-Password": observability.REDACTED,
                    "AcsApiKey": observability.REDACTED,
                }
            ],
            "Voucher_No": "123",
        }

    def test_credentials_reach_neither_the_log_nor_the_row(self, caplog):
        client = _client()
        # A hostile or careless caller passing credentials as params, and
        # ACS echoing one back.
        client._session.post.return_value = _response(
            200,
            {
                **_ok({"echo": "x"}),
                "ACSInputEcho": {"Company_Password": "SECRET_COMPANY_PASS"},
            },
        )

        with caplog.at_level(logging.DEBUG, logger="shipping_acs.client"):
            client._call(
                "ACS_Test",
                {
                    "Voucher_No": "1",
                    "Company_Password": "SECRET_COMPANY_PASS",
                    "AcsApiKey": "SECRET_API_KEY",
                },
            )

        rendered = " ".join(
            json.dumps(r.__dict__, default=str) for r in caplog.records
        )
        row = AcsApiCall.objects.get()
        stored = json.dumps([row.request_params, row.response])
        for secret in CREDENTIALS.values():
            assert secret not in rendered
            assert secret not in stored
        assert row.request_params["Company_Password"] == observability.REDACTED

    def test_the_wire_body_still_carries_the_credentials(self):
        client = _client()
        client._session.post.return_value = _response(200, _ok())
        client._call("ACS_Test", {"Voucher_No": "1"})
        sent = client._session.post.call_args.kwargs["json"]
        assert sent["ACSInputParameters"]["Company_ID"] == "SECRET_COMPANY_ID"

    def test_pdf_bodies_become_a_length_marker(self):
        client = _client()
        client._session.post.return_value = _response(
            200, _ok({"Voucher_Print": "A" * 50_000})
        )
        client._call("ACS_Print_Voucher", {"Voucher_No": "1"})

        row = AcsApiCall.objects.get()
        value = row.response["ACSOutputResponce"]["ACSValueOutput"][0]
        assert value["Voucher_Print"] == "<50000 chars omitted>"

    def test_oversized_payloads_are_capped(self):
        client = _client()
        rows = [{"k": f"value-{i}" * 5} for i in range(2000)]
        client._session.post.return_value = _response(
            200,
            {
                "ACSExecution_HasError": False,
                "ACSOutputResponce": {"ACSTableOutput": {"Table_Data": rows}},
            },
        )
        client._call("Acs_Stations", {})
        row = AcsApiCall.objects.get()
        assert row.response["_truncated"] is True


class TestCallHistory:
    def test_writes_one_row_with_the_identifiers(self):
        client = _client()
        client._session.post.return_value = _response(200, _ok())

        client._call("ACS_Get_Pickup_Lists", {"Pickup_Date": "2026-10-28"})

        row = AcsApiCall.objects.get()
        assert row.alias == "ACS_Get_Pickup_Lists"
        assert row.http_status == 200
        assert row.outcome == AcsApiCallOutcome.OK
        assert str(row.pickup_date) == "2026-10-28"
        assert row.duration_ms >= 0

    def test_a_failing_write_never_breaks_the_call(self, caplog):
        client = _client()
        client._session.post.return_value = _response(
            200, _ok({"hello": "world"})
        )

        with (
            patch.object(
                AcsApiCall.objects, "create", side_effect=RuntimeError("db")
            ),
            caplog.at_level(logging.WARNING, logger="shipping_acs.client"),
        ):
            result = client._call("ACS_Test", {})

        assert result["ACSValueOutput"][0]["hello"] == "world"
        assert any(
            "Could not record the ACS call history" in r.getMessage()
            for r in caplog.records
        )

    def test_a_failing_write_keeps_the_original_exception(self):
        client = _client()
        client._session.post.return_value = _response(406)

        with (
            patch.object(
                AcsApiCall.objects, "create", side_effect=RuntimeError("db")
            ),
            pytest.raises(AcsAuthError),
        ):
            client._call("ACS_Test", {})

    def test_a_failing_write_does_not_poison_an_open_transaction(self):
        from django.db import transaction

        client = _client()
        client._session.post.return_value = _response(200, _ok())

        with transaction.atomic():
            with patch.object(
                AcsApiCall.objects, "create", side_effect=RuntimeError("db")
            ):
                client._call("ACS_Test", {})
            # The outer transaction is still usable.
            assert AcsApiCall.objects.count() == 0

    def test_observation_errors_are_swallowed(self, caplog):
        client = _client()
        client._session.post.return_value = _response(200, _ok({"a": 1}))

        with (
            patch(
                "shipping_acs.observability.identifiers",
                side_effect=RuntimeError("bad"),
            ),
            caplog.at_level(logging.WARNING, logger="shipping_acs.client"),
        ):
            result = client._call("ACS_Test", {})

        assert result["ACSValueOutput"] == [{"a": 1}]


class TestRetention:
    def test_sweep_deletes_only_rows_past_the_window(self):
        from shipping_acs.tasks import purge_expired_acs_api_calls

        old = AcsApiCall.objects.create(alias="A", duration_ms=1, outcome="ok")
        fresh = AcsApiCall.objects.create(
            alias="B", duration_ms=1, outcome="ok"
        )
        AcsApiCall.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=91)
        )

        with override_settings(ACS_API_CALL_RETENTION_DAYS=90):
            result = purge_expired_acs_api_calls()

        assert result == {"deleted": 1}
        assert list(AcsApiCall.objects.values_list("pk", flat=True)) == [
            fresh.pk
        ]

    def test_fanout_reaches_suspended_stores_and_is_scheduled(self, settings):
        from tenant import tasks as tenant_tasks

        with patch("tenant.tasks.run_for_all_tenants") as run:
            tenant_tasks.fanout_purge_expired_acs_api_calls()

        run.assert_called_once_with(
            "shipping_acs.tasks.purge_expired_acs_api_calls",
            include_suspended=True,
        )
        assert (
            settings.CELERY_BEAT_SCHEDULE["purge-expired-acs-api-calls"]["task"]
            == "tenant.tasks.fanout_purge_expired_acs_api_calls"
        )
