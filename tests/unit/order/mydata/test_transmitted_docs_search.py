"""How a canceled receipt's cancellation is looked up in AADE
(``service.find_cancellation_mark``): bounded by the document's MARK and its
issue date, never by a page count, so a busy VAT is read to the end."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from order.accounting.mydata.exceptions import MyDataTransportError
from order.accounting.mydata.service import find_cancellation_mark
from tests.utils.mydata import mydata_enabled

MARK = 400000000000500
RECEIPT = SimpleNamespace(mydata_mark=MARK, issue_date=date(2026, 11, 19))


def _page(invoices: str, token: int | None) -> bytes:
    continuation = (
        "<continuationToken>"
        f"<nextPartitionKey>p{token}</nextPartitionKey>"
        f"<nextRowKey>r{token}</nextRowKey>"
        "</continuationToken>"
        if token is not None
        else ""
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<RequestedDoc xmlns="http://www.aade.gr/myDATA/invoice/v1.0">'
        f"<invoicesDoc>{invoices}</invoicesDoc>{continuation}"
        "</RequestedDoc>"
    ).encode()


def _other(mark: int) -> str:
    return f"<invoice><uid>u{mark}</uid><mark>{mark}</mark></invoice>"


def _search(pages):
    with (
        mydata_enabled(),
        patch(
            "order.accounting.mydata.client.MyDataClient."
            "request_transmitted_docs",
            side_effect=pages,
        ) as request,
    ):
        return find_cancellation_mark(RECEIPT), request


def test_a_busy_vat_is_read_to_the_end_of_the_day():
    """Thirty pages of other documents before the receipt: every one is
    read, where a cap of twenty pages answered "unknown" for ever."""
    pages = [_page(_other(MARK + 1 + n), token=n) for n in range(30)]
    pages.append(
        _page(
            f"<invoice><uid>u</uid><mark>{MARK}</mark>"
            "<cancelledByMark>900000000000001</cancelledByMark></invoice>",
            token=None,
        )
    )

    found, request = _search(pages)

    assert found == 900000000000001
    assert request.call_count == 31


def test_the_search_starts_at_the_receipt_and_is_bounded_by_its_issue_date():
    found, request = _search(
        [_page(f"<invoice><uid>u</uid><mark>{MARK}</mark></invoice>", None)]
    )

    assert found is None
    query = request.call_args.kwargs
    assert query["after_mark"] == MARK - 1
    assert query["date_from"] == date(2026, 11, 18)
    assert query["date_to"] == date(2026, 11, 20)


def test_a_receipt_aade_does_not_list_concludes_nothing():
    """It cannot say "not canceled" of a document it did not see: the
    receipt stays set aside and is asked about again."""
    with pytest.raises(MyDataTransportError):
        _search([_page(_other(MARK + 1), None)])


def test_a_continuation_token_handed_back_twice_stops_the_read():
    with pytest.raises(MyDataTransportError):
        _search([_page(_other(MARK + 1), token=7)] * 3)
