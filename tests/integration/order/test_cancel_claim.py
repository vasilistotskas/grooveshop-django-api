"""One myDATA cancel per document at a time.

``_cancel_registered_document`` sends ``CancelInvoice`` for a document staff
issued in error. Two confirmed clicks, or a staff cancel racing the
automatic settle of a canceled order's receipt, must not send it twice:
the cancel takes a claim per document (a receipt's is the settle path's own
key) and reads the document again once it holds it.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest
from celery.exceptions import Retry
from django.conf import settings
from django.core.cache import cache

from order.accounting.invoicing import generate_invoice
from order.accounting.mydata import MyDataTransportError
from order.enum.status import OrderStatus
from order.factories import OrderFactory
from order.models.history import OrderHistory
from order.models.invoice import MyDataStatus
from order.models.retail_credit import CreditReason, RetailCredit
from order.tasks import (
    _cancel_claim_key,
    _settle_claim_key,
    cancel_mydata_credit,
    cancel_mydata_invoice,
)
from tests.utils.mydata import FakeAade, issuable, mydata_enabled, registered

pytestmark = pytest.mark.django_db


def _receipt():
    order = issuable(OrderFactory(status=OrderStatus.PROCESSING))
    with mydata_enabled(auto_submit=False):
        invoice = registered(generate_invoice(order))
    return order, invoice


def _credit(*, status=None):
    order, invoice = _receipt()
    credit = RetailCredit.objects.create(
        order=order,
        invoice=invoice,
        reason=CreditReason.SETTLEMENT,
        credit_number="CRD-2026-000001",
        issue_date=date(2026, 3, 20),
        mydata_mark=400000000000002,
    )
    if status is not None:
        RetailCredit.objects.filter(pk=credit.pk).update(mydata_status=status)
        credit.refresh_from_db()
    return credit


def _held(key: str):
    claim = cache.lock(key, timeout=settings.MYDATA_SETTLE_CLAIM_SECONDS)
    assert claim.acquire()
    return claim


def _notes(order) -> int:
    return OrderHistory.objects.filter(order=order).count()


class TestTheClaim:
    def test_a_cancel_sends_once(self):
        credit = _credit()

        with mydata_enabled(), FakeAade() as aade:
            assert cancel_mydata_credit(credit.pk) is True

        assert len(aade.cancelled) == 1
        credit.refresh_from_db()
        assert credit.mydata_status == MyDataStatus.CANCELED

    def test_a_second_run_while_the_claim_is_held_sends_nothing(self):
        credit = _credit()
        claim = _held(_cancel_claim_key(credit.order, credit))

        try:
            with mydata_enabled(), FakeAade() as aade:
                result = cancel_mydata_credit(credit.pk)
        finally:
            claim.release()

        assert result is False
        assert aade.cancelled == []
        credit.refresh_from_db()
        assert credit.mydata_status != MyDataStatus.CANCELED

    def test_a_document_already_canceled_sends_nothing_and_logs_nothing(self):
        credit = _credit(status=MyDataStatus.CANCELED)
        before = _notes(credit.order)

        with mydata_enabled(), FakeAade() as aade:
            result = cancel_mydata_credit(credit.pk)

        assert result is True
        assert aade.cancelled == []
        assert _notes(credit.order) == before

    def test_the_receipt_cancel_and_the_settle_share_the_claim(self):
        order, invoice = _receipt()
        assert _cancel_claim_key(order, invoice) == _settle_claim_key(order.id)
        claim = _held(_settle_claim_key(order.id))

        try:
            with mydata_enabled(), FakeAade() as aade:
                result = cancel_mydata_invoice(order.id)
        finally:
            claim.release()

        assert result is False
        assert aade.cancelled == []

    def test_a_credit_has_a_claim_of_its_own(self):
        credit = _credit()

        assert _cancel_claim_key(credit.order, credit) != _settle_claim_key(
            credit.order.id
        )

    def test_a_retry_releases_the_claim(self):
        credit = _credit()

        with (
            mydata_enabled(),
            FakeAade(),
            patch(
                "order.accounting.mydata.cancel_document",
                side_effect=MyDataTransportError("timeout"),
            ),
            patch.object(cancel_mydata_credit, "retry", side_effect=Retry()),
            pytest.raises(Retry),
        ):
            cancel_mydata_credit(credit.pk)

        claim = cache.lock(
            _cancel_claim_key(credit.order, credit),
            timeout=settings.MYDATA_SETTLE_CLAIM_SECONDS,
        )
        assert claim.acquire()
        claim.release()
