"""The receipt's movement indication in the myDATA payload (#747).

A retail sale that moves through a courier gets its 11.1 with movement
indication (Α.1123/2024 Annex ΙΙ §7.3, as replaced by Α.1233/2026): the
header's ``dispatchDate``, ``movePurpose``, ``otherDeliveryNoteHeader`` and
``isDeliveryNote``, in the order of ``InvoiceHeaderType`` (myDATA v2.0.2
XSD), and the quantity and its unit on each goods line. A receipt whose
parcel left while myDATA could not be reached carries
``transmissionFailure`` 3 before the issuer.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch
from xml.etree.ElementTree import fromstring

import pytest
from djmoney.money import Money

from order.accounting.invoicing import generate_invoice
from order.accounting.movement import ReceiptMovement
from order.accounting.mydata.builder import build_receipt_xml
from order.factories.item import OrderItemFactory
from order.factories.order import OrderFactory
from product.factories.product import ProductFactory
from shipping.interfaces import PostalAddress
from tests.utils.mydata import AADE_NS, issuable, mydata_enabled
from vat.factories import VatFactory

pytestmark = pytest.mark.django_db

MOVEMENT = ReceiptMovement(
    dispatch_date=date(2026, 11, 19),
    loading=PostalAddress(
        street="Τατοΐου", number="5", postal_code="145 61", city="Κηφισιά"
    ),
    delivery=PostalAddress(
        street="Ερμού", number="10", postal_code="10563", city="Αθήνα"
    ),
)


def _receipt(shipping: str = "5.00"):
    order = OrderFactory(num_order_items=0)
    OrderItemFactory(
        order=order,
        product=ProductFactory(vat=VatFactory(value=24)),
        price=Money(Decimal("12.40"), "EUR"),
        quantity=3,
    )
    type(order).objects.filter(pk=order.pk).update(
        shipping_price=Money(Decimal(shipping), "EUR"),
        payment_method_fee=Money(0, "EUR"),
    )
    order.refresh_from_db()
    with (
        mydata_enabled(auto_submit=False),
        patch(
            "order.accounting.invoicing._render_pdf_bytes",
            return_value=b"%PDF-1.4 test",
        ),
    ):
        return generate_invoice(issuable(order))


def _build(invoice, movement=None):
    return build_receipt_xml(
        invoice,
        issuer_vat="123456789",
        issuer_country="GR",
        branch=0,
        series_prefix="GRVP",
        movement=movement,
    )


def _local(element) -> str:
    return element.tag.split("}", 1)[-1]


def test_the_header_carries_the_movement_in_the_schema_order():
    root = fromstring(_build(_receipt(), MOVEMENT).xml_bytes)
    header = root.find(f"{AADE_NS}invoice/{AADE_NS}invoiceHeader")
    tags = [_local(child) for child in header]

    assert tags[tags.index("currency") :] == [
        "currency",
        "dispatchDate",
        "movePurpose",
        "otherDeliveryNoteHeader",
        "isDeliveryNote",
    ]
    assert header.find(f"{AADE_NS}dispatchDate").text == "2026-11-19"
    # Optional in phase A, and when the courier collects is not known.
    assert header.find(f"{AADE_NS}dispatchTime") is None
    # Annex 8.14: 1 = «Πώληση».
    assert header.find(f"{AADE_NS}movePurpose").text == "1"
    assert header.find(f"{AADE_NS}isDeliveryNote").text == "true"


def test_the_loading_and_delivery_addresses_are_aade_address_types():
    root = fromstring(_build(_receipt(), MOVEMENT).xml_bytes)
    other = root.find(
        f"{AADE_NS}invoice/{AADE_NS}invoiceHeader/"
        f"{AADE_NS}otherDeliveryNoteHeader"
    )
    loading, delivery = list(other)

    assert _local(loading) == "loadingAddress"
    assert [(_local(e), e.text) for e in loading] == [
        ("street", "Τατοΐου"),
        ("number", "5"),
        # Without the space Greek postcodes are often written with.
        ("postalCode", "14561"),
        ("city", "Κηφισιά"),
    ]
    assert _local(delivery) == "deliveryAddress"
    assert delivery.find(f"{AADE_NS}city").text == "Αθήνα"


def test_goods_lines_carry_their_quantity_in_pieces_and_shipping_none():
    root = fromstring(_build(_receipt(), MOVEMENT).xml_bytes)
    goods, shipping = root.findall(f"{AADE_NS}invoice/{AADE_NS}invoiceDetails")

    assert [_local(e) for e in goods][:4] == [
        "lineNumber",
        "quantity",
        "measurementUnit",
        "netValue",
    ]
    assert goods.find(f"{AADE_NS}quantity").text == "3"
    # Annex 8.13: 1 = «Τεμάχια».
    assert goods.find(f"{AADE_NS}measurementUnit").text == "1"
    assert shipping.find(f"{AADE_NS}quantity") is None


def test_a_receipt_without_movement_is_unchanged():
    root = fromstring(_build(_receipt()).xml_bytes)
    invoice = root.find(f"{AADE_NS}invoice")

    for tag in (
        "dispatchDate",
        "movePurpose",
        "otherDeliveryNoteHeader",
        "isDeliveryNote",
    ):
        assert invoice.find(f"{AADE_NS}invoiceHeader/{AADE_NS}{tag}") is None
    assert invoice.find(f".//{AADE_NS}quantity") is None
    assert invoice.find(f"{AADE_NS}transmissionFailure") is None


def test_the_built_document_returns_the_movement_it_was_built_with():
    assert _build(_receipt(), MOVEMENT).movement == MOVEMENT
    assert _build(_receipt()).movement is None


def test_a_receipt_whose_parcel_left_without_connection_says_so_first():
    """«Απώλεια διασύνδεσης»: value 3, the one the REST API documentation
    (v2.0.2, §5) allows an ERP to send."""
    from django.utils import timezone

    invoice = _receipt()
    invoice.transmission_failed_at = timezone.now()
    root = fromstring(_build(invoice, MOVEMENT).xml_bytes)
    tags = [_local(child) for child in root.find(f"{AADE_NS}invoice")]

    assert tags[:2] == ["transmissionFailure", "issuer"]
    assert (
        root.find(f"{AADE_NS}invoice/{AADE_NS}transmissionFailure").text == "3"
    )
