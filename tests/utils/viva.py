"""The Viva Wallet HTTP boundary, for tests that refund a payment.

``requests.sessions.Session.send`` is the one seam that is patched: the
provider builds its real request (URL, query string, headers) and reads a
real ``requests.Response``, so an assertion on ``FakeVivaApi.deletes``
inspects what would have gone over the wire. The bodies below are copied
from Viva's Payment API spec
(https://developer.viva.com/apis-for-payments/payment-api/; a local copy,
gitignored, is kept at ``docs/viva-payment-api.yaml``): the
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


def retrieve_transaction(**overrides: Any) -> dict[str, Any]:
    """The Retrieve Transaction example with *overrides* applied."""
    return {**deepcopy(RETRIEVE_TRANSACTION), **overrides}


def retrieve_refund(
    *, amount: float, order_code: int, type_id: int = 7, **overrides: Any
) -> dict[str, Any]:
    """Retrieve Transaction answering for a REFUND transaction.

    Viva publishes no refund example for this call. The shape is the
    documented one with what the docs state about a refund: it has
    ``statusId`` ``F`` (``R`` belongs to the original payment), a refund
    or void ``transactionTypeId`` (4/7 for a card) and a negative
    ``amount`` ("transactions which reverse previous payments will always
    have a negative amount",
    developer.viva.com/integration-reference/response-codes/), and it
    carries the ``orderCode`` of the payment it reverses.
    """
    return retrieve_transaction(
        **{
            "amount": -amount,
            "orderCode": order_code,
            "statusId": "F",
            "transactionTypeId": type_id,
            **overrides,
        }
    )


# Transaction Reversal Created (EventTypeId 1797): the sample body of
# developer.viva.com/webhooks-for-payments/transaction-reversal-created.
# ``EventData.TransactionId`` is the REFUND, ``ParentId`` the payment it
# reverses; ``StatusId`` is ``F`` and ``TransactionTypeId`` 7 (Card Void).
# The sample's ``Amount`` of -1000 does not agree with its own
# ``OriginalAmount`` of -12, which is one more reason the handler reads the
# amount from Retrieve Transaction and never from this body.
REVERSAL_CREATED_EVENT: dict[str, Any] = {
    "Url": "Your webhook url",
    "EventData": {
        "Moto": False,
        "BinId": 42,
        "IsDcc": False,
        "Ucaf": None,
        "Email": "jdoe@vivawallet.com",
        "Phone": "799999999999",
        "BankId": "NET_VISA",
        "Systemic": False,
        "BatchId": "ea25a6d4-b1ab-4c44-813e-4a36e9f3b6b2",
        "Switching": False,
        "ParentId": "d406ca77-f045-4902-9176-506f56fcd793",
        "Amount": -1000,
        "ChannelId": "b8e0e6c6-3a7c-47c3-9db8-4725e96a4902",
        "TerminalId": 90000000,
        "MerchantId": "11ac9cd4-c9ea-48d3-b414-1f10ce664aad",
        "OrderCode": 3464811134270497,
        "ProductId": None,
        "StatusId": "F",
        "FullName": "John Doe",
        "ResellerId": None,
        "DualMessage": False,
        "InsDate": "2022-05-26T03:06:14.04",
        "TotalFee": 0,
        "CardToken": "2BFCB1FD4F000DB41CB5BA76CAFB68D76AB87752",
        "CardNumber": "414746XXXXXX0133",
        "Descriptor": None,
        "TipAmount": 0,
        "SourceCode": "Default",
        "SourceName": "Default",
        "Latitude": None,
        "Longitude": None,
        "CompanyName": "Luxembourg Services",
        "TransactionId": "addffca1-25d2-4739-acb4-b05e385e8563",
        "CompanyTitle": "Luxembourg Services",
        "PanEntryMode": "01",
        "ReferenceNumber": 66727,
        "ResponseCode": "00",
        "CurrencyCode": "978",
        "OrderCulture": "en-GB",
        "MerchantTrns": None,
        "CustomerTrns": None,
        "IsManualRefund": False,
        "TargetPersonId": None,
        "TargetWalletId": None,
        "AcquirerApproved": False,
        "LoyaltyTriggered": False,
        "TransactionTypeId": 7,
        "AuthorizationId": None,
        "TotalInstallments": 0,
        "CardCountryCode": "SG",
        "CardIssuingBank": "Citibank Singapore Ltd.",
        "RedeemedAmount": 0,
        "ClearanceDate": None,
        "ConversionRate": 1,
        "CurrentInstallment": 0,
        "OriginalAmount": -12,
        "Tags": ["Sample tag 3", "Sample tag 2", "Sample tag 1"],
        "BillId": None,
        "ConnectedAccountId": None,
        "ResellerSourceCode": None,
        "ResellerSourceName": None,
        "MerchantCategoryCode": 8099,
        "ResellerCompanyName": None,
        "CardUniqueReference": "5984325974181FFDE001404E14C6B88CF3613891",
        "OriginalCurrencyCode": "978",
        "ExternalTransactionId": None,
        "ResellerSourceAddress": None,
        "CardExpirationDate": "2030-12-31T00:00:00",
        "ServiceId": None,
        "RetrievalReferenceNumber": "214523066721",
        "AssignedMerchantUsers": [],
        "AssignedResellerUsers": [],
        "CardTypeId": 0,
        "ResponseEventId": None,
        "ElectronicCommerceIndicator": "7",
        "OrderServiceId": 0,
        "ApplicationIdentifierTerminal": None,
        "IntegrationId": None,
        "CardProductCategoryId": 1,
        "CardProductAccountTypeId": 1,
        "DigitalWalletId": 3,
        "DccSessionId": None,
        "DccMarkup": None,
        "DccDifferenceOverEcb": None,
    },
    "Created": "2022-05-26T00:06:14.2190894Z",
    "CorrelationId": "22-146-83163D56",
    "EventTypeId": 1797,
    "Delay": None,
    "RetryCount": 0,
    "RetryDelayInSeconds": None,
    "MessageId": "81911f2e-bd91-43d7-bf66-617e6191a1ce",
    "RecipientId": "11ac9cd4-c9ea-48d3-b414-1f10ce664aad",
    "MessageTypeId": 512,
}


def reversal_created_event(
    *, refund_id: str, parent_id: str, order_code: int
) -> dict[str, Any]:
    """The documented 1797 body, naming this refund, payment and order."""
    body = deepcopy(REVERSAL_CREATED_EVENT)
    body["EventData"].update(
        TransactionId=refund_id, ParentId=parent_id, OrderCode=order_code
    )
    return body


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
    ``transactions`` answers Retrieve for particular transaction ids (a
    refund and the payment it reverses are two transactions); any other id
    gets ``retrieve``.
    """

    def __init__(
        self,
        *,
        captured: float = 21.1,
        cancel: tuple[int, Any] = (200, CANCEL_SUCCESS),
        retrieve: tuple[int, Any] | None = None,
        transactions: dict[str, tuple[int, Any]] | None = None,
    ):
        self.cancel = cancel
        self.retrieve = retrieve or (
            200,
            {**deepcopy(RETRIEVE_TRANSACTION), "amount": captured},
        )
        self.transactions = transactions or {}
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
            transaction_id = path.rsplit("/", 1)[-1]
            return _response(
                request, *self.transactions.get(transaction_id, self.retrieve)
            )
        if request.method == "DELETE" and path.startswith("/api/transactions/"):
            return _response(request, *self.cancel)
        raise AssertionError(
            f"Unexpected Viva request: {request.method} {path}"
        )

    @property
    def retrieved(self) -> list[str]:
        """The transaction ids Retrieve Transaction was asked about."""
        return [
            urlsplit(r.url or "").path.rsplit("/", 1)[-1]
            for r in self.sent
            if r.method == "GET"
        ]

    @property
    def deletes(self) -> list[requests.PreparedRequest]:
        """The Cancel Transaction requests that were sent."""
        return [r for r in self.sent if r.method == "DELETE"]

    @staticmethod
    def query(request: requests.PreparedRequest) -> dict[str, list[str]]:
        return parse_qs(urlsplit(request.url or "").query)
