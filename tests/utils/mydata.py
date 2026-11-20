"""The myDATA boundary, for tests that issue retail documents.

``mydata_enabled`` makes the integration read as enabled and configured: the
config loader, which joins the store's settings with its credentials on the
tenant row, is the seam the service documents as patchable, and the store
settings read beside it come through ``Setting.get``; ``issuable`` turns an
order into one the platform issues a receipt for; ``FakeAade`` is AADE's
``SendInvoices`` / ``CancelInvoice``: it
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

from extra_settings.models import Setting

from order.accounting.mydata.config import MyDataConfig

AADE_NS = "{http://www.aade.gr/myDATA/invoice/v1.0}"
#: The seller VAT the store issues under (``INVOICE_SELLER_VAT_ID``).
ISSUER_VAT = "123456789"
FIRST_MARK = 400000000000001

READY_CONFIG = MyDataConfig(
    enabled=True,
    fhm_exemption_ground="ΠΟΛ.1002/2014 (test fixture)",
    takes_real_orders=True,
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
    is one that has not enabled myDATA at all. The store's ``MYDATA_ENABLED``
    switch, which gates the receipt and credit emails, reads as ``ready``.
    """
    config = dataclasses.replace(
        READY_CONFIG,
        auto_submit=auto_submit,
        enabled=ready,
        user_id=READY_CONFIG.user_id if ready else "",
    )
    # Every module holds its own bound reference to the loader.
    targets = (
        "order.accounting.mydata.config.load_config",
        "order.accounting.mydata.service.load_config",
        "order.signals.handlers.load_mydata_config",
        "order.accounting.invoicing.load_mydata_config",
    )
    # The store settings the integration reads, through ``Setting.get``
    # like every store setting: the switch the receipt and credit emails
    # are gated on, and the seller VAT every document is issued under.
    read_setting = Setting.get
    configured = {
        "MYDATA_ENABLED": config.enabled,
        "INVOICE_SELLER_VAT_ID": ISSUER_VAT,
    }

    def setting_as_configured(name: str, default=None):
        if name in configured:
            return configured[name]
        return read_setting(name, default=default)

    with ExitStack() as stack:
        stack.enter_context(
            mock.patch.object(Setting, "get", side_effect=setting_as_configured)
        )
        for target in targets:
            stack.enter_context(mock.patch(target, return_value=config))
        yield config


def issuable(order):
    """``order`` as ``order.accounting.invoicing.generate_invoice`` issues a receipt
    for it: a receipt order that has shipped (``receipt_issuable``).

    Written with ``update()``: shipping it through the state machine would
    signal ``order_shipped``, which dispatches the issuing task itself.
    """
    from order.enum.document_type import OrderDocumentTypeEnum
    from order.enum.status import OrderStatus

    type(order).objects.filter(pk=order.pk).update(
        document_type=OrderDocumentTypeEnum.RECEIPT.value,
        status=OrderStatus.SHIPPED,
    )
    order.document_type = OrderDocumentTypeEnum.RECEIPT.value
    order.status = OrderStatus.SHIPPED
    return order


def registered(invoice):
    """``invoice`` as AADE registered it (CONFIRMED, with a MARK): the one
    state in which a receipt reaches the customer
    (``Invoice.is_deliverable``)."""
    from order.models.invoice import MyDataStatus

    type(invoice).objects.filter(pk=invoice.pk).update(
        mydata_status=MyDataStatus.CONFIRMED,
        mydata_mark=invoice.mydata_mark or FIRST_MARK,
    )
    invoice.refresh_from_db()
    return invoice


def _doc(xml_bytes: bytes) -> SimpleNamespace:
    root = fromstring(xml_bytes)
    invoice = root.find(f"{AADE_NS}invoice")
    header = invoice.find(f"{AADE_NS}invoiceHeader")

    def text(parent, tag):
        el = parent.find(f"{AADE_NS}{tag}")
        return None if el is None else el.text

    summary = invoice.find(f"{AADE_NS}invoiceSummary")
    return SimpleNamespace(
        xml=xml_bytes,
        is_delivery_note=text(header, "isDeliveryNote") == "true",
        transmission_failure=text(invoice, "transmissionFailure"),
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
    Success and the next MARK, every ``CancelInvoice`` with a cancellation
    MARK (a second cancel of one document with AADE's refusal), and
    ``RequestTransmittedDocs`` lists the documents it registered, each
    with its ``cancelledByMark`` once canceled, and the cancellations it
    holds.

    ``lose_cancel_answers=True`` registers each cancel and then loses the
    answer (a ``MyDataTransportError``), as a dropped connection does.
    """

    def __init__(self, *, lose_cancel_answers: bool = False):
        self.sent: list[SimpleNamespace] = []
        self.marks: list[int] = []
        self.cancelled: list[int] = []
        self.cancellations: dict[int, int] = {}
        #: Documents AADE registered whose answer we never read, by uid:
        #: listed by ``RequestTransmittedDocs``.
        self.registered: dict[str, int] = {}
        self.lose_cancel_answers = lose_cancel_answers
        self._next = FIRST_MARK
        self._patches = [
            mock.patch(
                "order.accounting.mydata.client.MyDataClient.send_invoices",
                side_effect=self._send,
            ),
            mock.patch(
                "order.accounting.mydata.client.MyDataClient.cancel_invoice",
                side_effect=self._cancel,
            ),
            mock.patch(
                "order.accounting.mydata.client.MyDataClient."
                "request_transmitted_docs",
                side_effect=self._transmitted,
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
        from order.accounting.mydata.exceptions import MyDataTransportError

        if mark in self.cancellations:
            return (
                '<?xml version="1.0" encoding="UTF-8"?><ResponseDoc><response>'
                "<index>1</index><statusCode>ValidationError</statusCode>"
                "<errors><error><message>Invoice with MARK "
                f"{mark} cannot be cancelled</message><code>279</code>"
                "</error></errors></response></ResponseDoc>"
            ).encode()
        self.cancelled.append(mark)
        cancellation = 900000000000000 + len(self.cancelled)
        self.cancellations[mark] = cancellation
        if self.lose_cancel_answers:
            raise MyDataTransportError("the answer was lost")
        return (
            '<?xml version="1.0" encoding="UTF-8"?><ResponseDoc><response>'
            "<index>1</index><statusCode>Success</statusCode>"
            f"<cancellationMark>{cancellation}"
            "</cancellationMark></response></ResponseDoc>"
        ).encode()

    def _transmitted(self, *, after_mark: int = 0, **_query) -> bytes:
        rows = "".join(
            f"<cancelledInvoice><invoiceMark>{mark}</invoiceMark>"
            f"<cancellationMark>{cancellation}</cancellationMark>"
            "<cancellationDate>2026-11-18</cancellationDate>"
            "</cancelledInvoice>"
            for mark, cancellation in self.cancellations.items()
            if mark > after_mark
        )
        listed = {
            mark: f"uid{mark}" for mark in self.marks if mark > after_mark
        } | {
            mark: uid
            for uid, mark in self.registered.items()
            if mark > after_mark
        }
        invoices = "".join(
            f"<invoice><uid>{uid}</uid><mark>{mark}</mark>"
            + (
                f"<cancelledByMark>{self.cancellations[mark]}</cancelledByMark>"
                if mark in self.cancellations
                else ""
            )
            + "</invoice>"
            for mark, uid in listed.items()
        )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<RequestedDoc xmlns="http://www.aade.gr/myDATA/invoice/v1.0">'
            f"<invoicesDoc>{invoices}</invoicesDoc>"
            f"<cancelledInvoicesDoc>{rows}</cancelledInvoicesDoc>"
            "</RequestedDoc>"
        ).encode()

    @property
    def receipts(self) -> list[SimpleNamespace]:
        return [d for d in self.sent if d.invoice_type == "11.1"]

    @property
    def credits(self) -> list[SimpleNamespace]:
        return [d for d in self.sent if d.invoice_type == "11.4"]
