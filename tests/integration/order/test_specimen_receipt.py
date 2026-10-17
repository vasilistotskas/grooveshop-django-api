"""The demo store's specimen receipt («ΔΕΙΓΜΑ»): the real receipt document,
watermarked, with no QR, rendered on demand for a demo store only.

Three things are pinned: a specimen is never issued or submitted (no row,
no counter slot, no myDATA call), never stored, and never offered on a
store that is not a demo store. WeasyPrint's native stack is not on every
machine, so the render step is replaced where bytes are needed and the
template is rendered to HTML where the document's content is.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.core.cache.backends.locmem import LocMemCache
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import translation
from rest_framework import status
from rest_framework.test import APIClient

from core.api.throttling import OrderSpecimenThrottle
from order.enum.status import OrderStatus
from order.factories.order import OrderFactory
from order.invoicing import (
    SpecimenNotAvailable,
    _build_context,
    _compute_vat_breakdown,
    _order_totals,
    render_specimen_pdf,
    specimen_available,
)
from order.models.invoice import Invoice, InvoiceCounter
from tests.utils.mydata import issuable
from user.factories.account import UserAccountFactory

FAKE_PDF = b"%PDF-1.4\n%SPECIMEN\n%EOF"

pytestmark = pytest.mark.django_db


@pytest.fixture
def demo_store(bind_tenant):
    bind_tenant(SimpleNamespace(is_demo=True, schema_name="demo"))


@pytest.fixture
def real_store(bind_tenant):
    bind_tenant(SimpleNamespace(is_demo=False, schema_name="shop"))


@pytest.fixture
def user():
    return UserAccountFactory()


@pytest.fixture
def client(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def _download_url(order) -> str:
    return reverse("order-specimen-download", kwargs={"pk": order.pk})


def _detail_url(order) -> str:
    return reverse("order-detail", kwargs={"pk": order.pk})


def _html(order, specimen_flag: bool) -> str:
    breakdown = _compute_vat_breakdown(order)
    totals = _order_totals(order, breakdown)
    invoice = Invoice(
        order=order,
        invoice_number="SPECIMEN",
        issue_date="2026-10-17",
        seller_snapshot={"name": "Demo"},
        buyer_snapshot={"name": "Buyer"},
        currency="EUR",
    )
    return render_to_string(
        "invoices/invoice.html",
        _build_context(
            order, invoice, breakdown, totals, specimen=specimen_flag
        ),
    )


def test_specimen_download_streams_a_pdf_on_a_demo_store(
    demo_store, client, user
) -> None:
    order = issuable(OrderFactory(user=user))

    with patch(
        "order.invoicing._render_pdf_bytes", return_value=FAKE_PDF
    ) as render:
        response = client.get(_download_url(order))

    assert response.status_code == status.HTTP_200_OK
    assert response["Content-Type"] == "application/pdf"
    assert b"".join(response.streaming_content) == FAKE_PDF
    assert render.call_args.args[0]["specimen"] is True


def test_specimen_is_never_issued_stored_or_submitted(
    demo_store, client, user
) -> None:
    order = issuable(OrderFactory(user=user))

    with (
        patch("order.invoicing._render_pdf_bytes", return_value=FAKE_PDF),
        patch("order.invoicing._persist_invoice_row") as persist,
        patch("order.mydata.service.submit_document") as submit,
    ):
        response = client.get(_download_url(order))

    assert response.status_code == status.HTTP_200_OK
    persist.assert_not_called()
    submit.assert_not_called()
    assert not Invoice.objects.exists()
    assert not InvoiceCounter.objects.exists()


def test_specimen_is_refused_on_a_store_that_is_not_a_demo_store(
    real_store, client, user
) -> None:
    order = issuable(OrderFactory(user=user))

    with patch("order.invoicing._render_pdf_bytes") as render:
        response = client.get(_download_url(order))

    assert response.status_code == status.HTTP_404_NOT_FOUND
    render.assert_not_called()
    with pytest.raises(SpecimenNotAvailable):
        render_specimen_pdf(order)


def test_specimen_is_refused_with_no_store_bound(client, user) -> None:
    order = issuable(OrderFactory(user=user))

    assert specimen_available(order) is False
    assert client.get(_download_url(order)).status_code == (
        status.HTTP_404_NOT_FOUND
    )


def test_specimen_is_refused_until_the_order_has_shipped(
    demo_store, client, user
) -> None:
    order = OrderFactory(user=user, status=OrderStatus.PROCESSING)

    assert client.get(_download_url(order)).status_code == (
        status.HTTP_404_NOT_FOUND
    )


def test_another_customer_cannot_download_the_specimen(
    demo_store, client
) -> None:
    order = issuable(OrderFactory(user=UserAccountFactory()))

    assert client.get(_download_url(order)).status_code == (
        status.HTTP_403_FORBIDDEN
    )


def test_order_detail_offers_the_specimen_on_a_demo_store_only(
    bind_tenant, client, user
) -> None:
    order = issuable(OrderFactory(user=user))

    bind_tenant(SimpleNamespace(is_demo=True, schema_name="demo"))
    assert client.get(_detail_url(order)).json()["hasSpecimenReceipt"] is True

    bind_tenant(SimpleNamespace(is_demo=False, schema_name="shop"))
    assert client.get(_detail_url(order)).json()["hasSpecimenReceipt"] is False


def test_specimen_document_has_a_watermark_and_no_qr(user) -> None:
    order = issuable(OrderFactory(user=user))

    html = _html(order, specimen_flag=True)

    assert 'class="specimen-watermark"' in html
    assert "<svg" not in html
    assert 'class="qr"' not in html


def test_the_real_receipt_has_no_watermark_and_keeps_its_qr(user) -> None:
    order = issuable(OrderFactory(user=user))

    html = _html(order, specimen_flag=False)

    assert 'class="specimen-watermark"' not in html
    assert "<svg" in html


@pytest.mark.parametrize(
    ("language", "watermark", "notice"),
    [
        ("el", "ΔΕΙΓΜΑ", "ΔΕΙΓΜΑ - δεν αποτελεί φορολογικό παραστατικό"),
        ("en", "SPECIMEN", "SPECIMEN - not a tax document"),
    ],
)
def test_specimen_watermark_and_notice_are_translated(
    user, language, watermark, notice
) -> None:
    order = issuable(OrderFactory(user=user))

    with translation.override(language):
        html = _html(order, specimen_flag=True)

    assert f'<div class="specimen-watermark">{watermark}</div>' in html
    assert notice in html


def test_a_tenant_stand_in_without_the_flag_is_not_a_demo_store(
    bind_tenant, client, user
) -> None:
    """Tests bind credential-only stand-ins; the flag read must not fail
    on one (order creation serializes ``hasSpecimenReceipt``)."""
    bind_tenant(SimpleNamespace(schema_name="stand-in"))
    order = issuable(OrderFactory(user=user))

    response = client.get(_detail_url(order))

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["hasSpecimenReceipt"] is False


def test_the_specimen_download_is_throttled(
    demo_store, client, user, monkeypatch
) -> None:
    """Each call renders a PDF on the request worker, so the Nth call in
    the window is refused. A private in-memory cache keeps other xdist
    workers from resetting the history."""
    rate = 3
    monkeypatch.setattr(
        OrderSpecimenThrottle, "rate", f"{rate}/minute", raising=False
    )
    monkeypatch.setattr(
        OrderSpecimenThrottle,
        "cache",
        LocMemCache(f"specimen-throttle-{uuid4()}", {}),
        raising=False,
    )
    order = issuable(OrderFactory(user=user))

    with patch("order.invoicing._render_pdf_bytes", return_value=FAKE_PDF):
        statuses = [
            client.get(_download_url(order)).status_code
            for _ in range(rate + 2)
        ]

    assert statuses == [
        *[status.HTTP_200_OK] * rate,
        *[status.HTTP_429_TOO_MANY_REQUESTS] * 2,
    ]
