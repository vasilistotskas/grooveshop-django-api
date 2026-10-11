"""The Viva Wallet HTTP boundary, for tests that refund a payment.

``requests.sessions.Session.send`` is the one seam that is patched: the
provider builds its real request (URL, query string, headers) and reads a
real ``requests.Response``, so an assertion on ``FakeVivaApi.deletes``
inspects what would have gone over the wire. The bodies below are copied
from the Payment API spec (``docs/viva-payment-api.yaml``): the
``delete_transaction`` example for Cancel Transaction and the
``get_transactions_v2`` example for Retrieve Transaction.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Self
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import requests

# Cancel Transaction, ``delete_transaction`` example: a successful call.
# ``TransactionId`` is the NEW refund transaction, not the payment.
CANCEL_SUCCESS: dict[str, Any] = {
    "Emv": None,
    "Amount": 2.0,
    "StatusId": "F",
    "CurrencyCode": 826,
    "TransactionId": "bd5390a9-5870-4d8d-abea-7f5a94f9cecc",
    "ReferenceNumber": 909196,
    "AuthorizationId": None,
    "RetrievalReferenceNumber": 122409743701,
    "ThreeDSecureStatusId": 2,
    "ErrorCode": 0,
    "ErrorText": None,
    "TimeStamp": "2021-08-12T15:11:11.2135839+03:00",
    "CorrelationId": None,
    "EventId": 0,
    "Success": True,
}

# The same schema describing an unsuccessful call: ``ErrorCode`` is
# non-zero with a descriptive ``ErrorText``, ``EventId`` is generated, and
# there is no refund transaction. It travels as HTTP 200. The spec fixes
# the fields, not a concrete failing code, so any non-zero value takes the
# same branch.
CANCEL_REFUSED: dict[str, Any] = {
    **CANCEL_SUCCESS,
    "Amount": 0.0,
    "StatusId": None,
    "TransactionId": None,
    "ErrorCode": 1403,
    "ErrorText": "The transaction cannot be reversed.",
    "EventId": 2701,
    "Success": False,
}

# Cancel Transaction's documented HTTP 403 body ("Transaction already
# cancelled"); the body is the message, with no JSON envelope fields.
CANCEL_ALREADY_CANCELLED = (
    "PaymentsNonReversibleTransaction: Request to reverse transaction aborted"
)

# Retrieve Transaction, ``get_transactions_v2`` example.
RETRIEVE_TRANSACTION: dict[str, Any] = {
    "email": "someone@vivawallet.com",
    "bankId": "NET_VISA",
    "amount": 21.1,
    "conversionRate": 1.2,
    "originalAmount": 10.55,
    "originalCurrencyCode": "978",
    "sourceCode": "3891",
    "switching": False,
    "orderCode": 6810910170372602,
    "statusId": "F",
    "fullName": "George Seferis",
    "insDate": "2021-12-03T17:41:11.153+02:00",
    "cardNumber": "523929XXXXXX0168",
    "currencyCode": "978",
    "customerTrns": "This is a description displayed to the customer",
    "merchantTrns": "This is a short description that helps you uniquely "
    "identify the transaction",
    "transactionTypeId": 5,
    "recurringSupport": False,
    "totalInstallments": 0,
    "cardCountryCode": "GR",
}

CREDENTIALS = {
    "merchant_id": "merchant-id",
    "api_key": "api-key",
    "client_id": "client-id",
    "client_secret": "client-secret",
    "webhook_verification_key": "",
    "source_code": "",
    "live_mode": False,
}


def _response(
    request: requests.PreparedRequest, status: int, body: Any
) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.reason = "OK" if status < 400 else "Error"
    response.url = request.url or ""
    response.request = request
    response.encoding = "utf-8"
    response._content = (
        body.encode() if isinstance(body, str) else json.dumps(body).encode()
    )
    return response


class FakeVivaApi:
    """A stand-in Viva: answers token, Retrieve and Cancel Transaction.

    ``captured`` is the amount Retrieve Transaction reports for the
    payment, in euros. ``cancel`` is the ``(HTTP status, body)`` Cancel
    Transaction answers with, ``retrieve`` the same for Retrieve.
    """

    def __init__(
        self,
        *,
        captured: float = 21.1,
        cancel: tuple[int, Any] = (200, CANCEL_SUCCESS),
        retrieve: tuple[int, Any] | None = None,
    ):
        self.cancel = cancel
        self.retrieve = retrieve or (
            200,
            {**deepcopy(RETRIEVE_TRANSACTION), "amount": captured},
        )
        self.sent: list[requests.PreparedRequest] = []
        self._patches = [
            mock.patch(
                "tenant.credentials.viva_wallet_credentials",
                return_value=CREDENTIALS,
            ),
            mock.patch(
                "requests.sessions.Session.send",
                autospec=True,
                side_effect=self._send,
            ),
        ]

    def __enter__(self) -> Self:
        for patch in self._patches:
            patch.start()
        return self

    def __exit__(self, *exc_info) -> None:
        for patch in reversed(self._patches):
            patch.stop()

    def _send(
        self,
        _session: requests.Session,
        request: requests.PreparedRequest,
        **_kwargs: Any,
    ) -> requests.Response:
        self.sent.append(request)
        path = urlsplit(request.url or "").path
        if request.method == "POST" and path.endswith("/connect/token"):
            return _response(
                request,
                200,
                {"access_token": "access-token", "expires_in": 3600},
            )
        if request.method == "GET" and path.startswith(
            "/checkout/v2/transactions/"
        ):
            return _response(request, *self.retrieve)
        if request.method == "DELETE" and path.startswith("/api/transactions/"):
            return _response(request, *self.cancel)
        raise AssertionError(
            f"Unexpected Viva request: {request.method} {path}"
        )

    @property
    def deletes(self) -> list[requests.PreparedRequest]:
        """The Cancel Transaction requests that were sent."""
        return [r for r in self.sent if r.method == "DELETE"]

    @staticmethod
    def query(request: requests.PreparedRequest) -> dict[str, list[str]]:
        return parse_qs(urlsplit(request.url or "").query)
