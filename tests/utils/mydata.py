"""The myDATA boundary, for tests that issue retail documents.

``mydata_enabled`` makes the integration read as enabled and configured (the
config loader and the seller VAT are the seams the service documents as
patchable); ``FakeAade`` is AADE's ``SendInvoices`` / ``CancelInvoice``: it
records every payload it receives, parsed, and answers each with a Success
carrying a new MARK. Nothing here reaches the network.
"""

from __future__ import annotations

import dataclasses
from contextlib import ExitStack, contextmanager
from decimal import Decimal
from types import SimpleNamespace
from typing import Self
from unittest import mock
from xml.etree.ElementTree import fromstring

from order.mydata.config import MyDataConfig

AADE_NS = "{http://www.aade.gr/myDATA/invoice/v1.0}"
FIRST_MARK = 400000000000001

READY_CONFIG = MyDataConfig(
    enabled=True,
    auto_submit=True,
    environment="dev",
    user_id="test-user",
    subscription_key="test-subscription-key",
    invoice_series_prefix="GRVP",
    credit_series_prefix="GRVC",
    issuer_branch=0,
    request_timeout_seconds=30,
)


@contextmanager
def mydata_enabled(*, auto_submit: bool = True, ready: bool = True):
    """The integration as a store with credentials sees it.

    ``auto_submit=False`` is a store that issues by hand; ``ready=False``
    is one that has not enabled myDATA at all.
    """
    config = dataclasses.replace(
        READY_CONFIG,
        auto_submit=auto_submit,
        enabled=ready,
        user_id=READY_CONFIG.user_id if ready else "",
    )
    # Every module holds its own bound reference to the loader.
    targets = (
        "order.mydata.config.load_config",
        "order.mydata.service.load_config",
        "order.signals.handlers.load_mydata_config",
    )
    with ExitStack() as stack:
        for target in targets:
            stack.enter_context(mock.patch(target, return_value=config))
        stack.enter_context(
            mock.patch(
                "order.mydata.service._resolve_issuer_vat",
                return_value="123456789",
            )
        )
        yield config


def _doc(xml_bytes: bytes) -> SimpleNamespace:
    root = fromstring(xml_bytes)
    invoice = root.find(f"{AADE_NS}invoice")
    header = invoice.find(f"{AADE_NS}invoiceHeader")

    def text(parent, tag):
        el = parent.find(f"{AADE_NS}{tag}")
        return None if el is None else el.text

    summary = invoice.find(f"{AADE_NS}invoiceSummary")
    return SimpleNamespace(
        invoice_type=text(header, "invoiceType"),
        correlated=text(header, "correlatedInvoices"),
        series=text(header, "series"),
        aa=text(header, "aa"),
        lines=[
            SimpleNamespace(
                net=Decimal(text(row, "netValue")),
                vat=Decimal(text(row, "vatAmount")),
                category=text(row, "vatCategory"),
            )
            for row in invoice.findall(f"{AADE_NS}invoiceDetails")
        ],
        net=Decimal(text(summary, "totalNetValue")),
        vat=Decimal(text(summary, "totalVatAmount")),
        gross=Decimal(text(summary, "totalGrossValue")),
        payments=[
            (int(text(p, "type")), Decimal(text(p, "amount")))
            for p in invoice.find(f"{AADE_NS}paymentMethods")
        ],
    )


class FakeAade:
    """AADE's ERP endpoints: every ``SendInvoices`` is answered with a
    Success and the next MARK, every ``CancelInvoice`` with a
    cancellation MARK."""

    def __init__(self, *, over_credit_error: bool = False):
        self.sent: list[SimpleNamespace] = []
        self.marks: list[int] = []
        self.cancelled: list[int] = []
        self._next = FIRST_MARK
        self._patches = [
            mock.patch(
                "order.mydata.client.MyDataClient.send_invoices",
                side_effect=self._send,
            ),
            mock.patch(
                "order.mydata.client.MyDataClient.cancel_invoice",
                side_effect=self._cancel,
            ),
        ]

    def __enter__(self) -> Self:
        for patch in self._patches:
            patch.start()
        return self

    def __exit__(self, *exc) -> None:
        for patch in reversed(self._patches):
            patch.stop()

    def _send(self, xml_bytes: bytes) -> bytes:
        self.sent.append(_doc(xml_bytes))
        mark = self._next
        self._next += 1
        self.marks.append(mark)
        return (
            '<?xml version="1.0" encoding="UTF-8"?><ResponseDoc><response>'
            "<index>1</index><statusCode>Success</statusCode>"
            f"<invoiceUid>uid{mark}</invoiceUid><invoiceMark>{mark}"
            "</invoiceMark><qrUrl>https://www1.aade.gr/mydata/qr/"
            f"{mark}</qrUrl></response></ResponseDoc>"
        ).encode()

    def _cancel(self, mark: int, entity_vat_number: str | None = None) -> bytes:
        self.cancelled.append(mark)
        return (
            '<?xml version="1.0" encoding="UTF-8"?><ResponseDoc><response>'
            "<index>1</index><statusCode>Success</statusCode>"
            f"<cancellationMark>{900000000000000 + len(self.cancelled)}"
            "</cancellationMark></response></ResponseDoc>"
        ).encode()

    @property
    def receipts(self) -> list[SimpleNamespace]:
        return [d for d in self.sent if d.invoice_type == "11.1"]

    @property
    def credits(self) -> list[SimpleNamespace]:
        return [d for d in self.sent if d.invoice_type == "11.4"]
