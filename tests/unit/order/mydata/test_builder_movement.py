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

from dataclasses import replace
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from xml.etree.ElementTree import fromstring

import pytest
from djmoney.money import Money

from order.accounting.invoicing import generate_invoice
from order.accounting.movement import (
    MovementParty,
    ReceiptMovement,
    moving_goods,
)
from order.accounting.mydata.builder import build_receipt_xml
from order.accounting.mydata.validator import validate_invoice_doc
from order.factories.item import OrderItemFactory
from order.factories.order import OrderFactory
from product.factories.product import ProductFactory
from shipping.interfaces import PostalAddress
from tests.utils.mydata import AADE_NS, issuable, mydata_enabled
from vat.factories import VatFactory

pytestmark = pytest.mark.django_db

#: The dispatch point, which is also the issuer's address
#: (``movement._issuer``).
DISPATCH_POINT = PostalAddress(
    street="Τατοΐου", street_number="5", postal_code="145 61", city="Κηφισιά"
)

MOVEMENT = ReceiptMovement(
    dispatch_date=date(2026, 11, 19),
    loading=DISPATCH_POINT,
    delivery=PostalAddress(
        street="Ερμού", street_number="10", postal_code="10563", city="Αθήνα"
    ),
    goods=(),
    issuer=MovementParty(
        name="Κατάστημα ΑΕ",
        country="GR",
        address=DISPATCH_POINT,
    ),
    recipient=MovementParty(
        name="Μαρία Παπαδοπούλου",
        country="GR",
        address=PostalAddress(
            street="Ερμού",
            street_number="10",
            postal_code="105 63",
            city="Αθήνα",
        ),
    ),
)


def _moving(invoice) -> ReceiptMovement:
    """``MOVEMENT`` carrying the goods of *invoice*'s order."""
    return replace(MOVEMENT, goods=moving_goods(invoice.order))


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


def _with_movement(invoice):
    return invoice, _moving(invoice)


def _local(element) -> str:
    return element.tag.split("}", 1)[-1]


def test_the_header_carries_the_movement_in_the_schema_order():
    root = fromstring(_build(*_with_movement(_receipt())).xml_bytes)
    header = root.find(f"{AADE_NS}invoice/{AADE_NS}invoiceHeader")
    tags = [_local(child) for child in header]

    assert tags[tags.index("currency") :] == [
        "currency",
        "dispatchDate",
        "movePurpose",
        "otherDeliveryNoteHeader",
        "isDeliveryNote",
        "nonObligatedRecipient",
    ]
    assert header.find(f"{AADE_NS}dispatchDate").text == "2026-11-19"
    # Optional in phase A, and when the courier collects is not known.
    assert header.find(f"{AADE_NS}dispatchTime") is None
    # Annex 8.14: 1 = «Πώληση».
    assert header.find(f"{AADE_NS}movePurpose").text == "1"
    assert header.find(f"{AADE_NS}isDeliveryNote").text == "true"


def test_the_loading_and_delivery_addresses_are_aade_address_types():
    root = fromstring(_build(*_with_movement(_receipt())).xml_bytes)
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
    root = fromstring(_build(*_with_movement(_receipt())).xml_bytes)
    goods, shipping = root.findall(f"{AADE_NS}invoice/{AADE_NS}invoiceDetails")

    assert [_local(e) for e in goods][:5] == [
        "lineNumber",
        "itemDescr",
        "quantity",
        "measurementUnit",
        "netValue",
    ]
    assert goods.find(f"{AADE_NS}quantity").text == "3"
    # Annex 8.13: 1 = «Τεμάχια».
    assert goods.find(f"{AADE_NS}measurementUnit").text == "1"
    assert shipping.find(f"{AADE_NS}quantity") is None


def test_it_names_what_aade_requires_of_a_movement_document():
    """AADE's sandbox (2026-11-20) refused every 11.1 with
    ``isDeliveryNote`` without these, although the XSD makes them optional:
    error 230, an ``itemDescr`` on every line; error 204, the issuer's name
    and address, and a counterpart with its name and address. A consumer
    has no VAT number: ``000000000`` (error 289), and
    ``nonObligatedRecipient``, since they take no part in the digital
    movement process. Never ``withoutDigitalTransportTracking``: AADE
    cannot cancel such a document (error 801)."""
    invoice, movement = _with_movement(_receipt())
    root = fromstring(_build(invoice, movement).xml_bytes)
    document = root.find(f"{AADE_NS}invoice")
    issuer = document.find(f"{AADE_NS}issuer")
    counterpart = document.find(f"{AADE_NS}counterpart")
    header = document.find(f"{AADE_NS}invoiceHeader")

    def shape(element):
        return [
            (_local(e), e.text if len(e) == 0 else shape(e)) for e in element
        ]

    assert [_local(e) for e in document][:3] == [
        "issuer",
        "counterpart",
        "invoiceHeader",
    ]
    assert shape(issuer) == [
        ("vatNumber", "123456789"),
        ("country", "GR"),
        ("branch", "0"),
        ("name", "Κατάστημα ΑΕ"),
        (
            "address",
            [
                ("street", "Τατοΐου"),
                ("number", "5"),
                ("postalCode", "14561"),
                ("city", "Κηφισιά"),
            ],
        ),
    ]
    assert shape(counterpart) == [
        ("vatNumber", "000000000"),
        ("country", "GR"),
        ("branch", "0"),
        ("name", "Μαρία Παπαδοπούλου"),
        (
            "address",
            [
                ("street", "Ερμού"),
                ("number", "10"),
                ("postalCode", "10563"),
                ("city", "Αθήνα"),
            ],
        ),
    ]
    assert [_local(e) for e in header][-2:] == [
        "isDeliveryNote",
        "nonObligatedRecipient",
    ]
    assert _text(header, "nonObligatedRecipient") == "true"
    assert header.find(f"{AADE_NS}withoutDigitalTransportTracking") is None
    goods, shipping = _details(root)
    assert [_local(e) for e in goods][:2] == ["lineNumber", "itemDescr"]
    assert _text(goods, "itemDescr") == invoice.order.items.get().product_name
    assert [_local(e) for e in shipping][:3] == [
        "lineNumber",
        "itemDescr",
        "netValue",
    ]
    assert _text(shipping, "itemDescr")
    validate_invoice_doc(_build(invoice, movement).xml_bytes)


def test_a_receipt_without_movement_is_unchanged():
    root = fromstring(_build(_receipt()).xml_bytes)
    invoice = root.find(f"{AADE_NS}invoice")

    for tag in (
        "dispatchDate",
        "movePurpose",
        "otherDeliveryNoteHeader",
        "isDeliveryNote",
        "nonObligatedRecipient",
    ):
        assert invoice.find(f"{AADE_NS}invoiceHeader/{AADE_NS}{tag}") is None
    assert invoice.find(f".//{AADE_NS}quantity") is None
    # AADE refuses a Greek issuer's name (219) and every itemDescr outside
    # a movement document.
    assert invoice.find(f"{AADE_NS}counterpart") is None
    assert invoice.find(f"{AADE_NS}issuer/{AADE_NS}name") is None
    assert invoice.find(f".//{AADE_NS}itemDescr") is None
    assert invoice.find(f"{AADE_NS}transmissionFailure") is None


def test_the_built_document_returns_the_movement_it_was_built_with():
    invoice, movement = _with_movement(_receipt())

    assert _build(invoice, movement).movement == movement
    assert _build(_receipt()).movement is None


def test_a_receipt_whose_parcel_left_without_connection_says_so_first():
    """«Απώλεια διασύνδεσης»: value 3, the one the REST API documentation
    (v2.0.2, §5) allows an ERP to send."""
    from django.utils import timezone

    invoice = _receipt()
    invoice.transmission_failed_at = timezone.now()
    root = fromstring(_build(invoice, _moving(invoice)).xml_bytes)
    tags = [_local(child) for child in root.find(f"{AADE_NS}invoice")]

    assert tags[:2] == ["transmissionFailure", "issuer"]
    assert (
        root.find(f"{AADE_NS}invoice/{AADE_NS}transmissionFailure").text == "3"
    )


def _details(root):
    return root.findall(f"{AADE_NS}invoice/{AADE_NS}invoiceDetails")


def _text(element, tag):
    found = element.find(f"{AADE_NS}{tag}")
    return None if found is None else found.text


def _summary(root, tag) -> Decimal:
    return Decimal(
        root.find(
            f"{AADE_NS}invoice/{AADE_NS}invoiceSummary/{AADE_NS}{tag}"
        ).text
    )


def test_a_free_gift_moves_on_a_line_of_no_value():
    """A gift has no money line (``taxation.order_vat_lines``), but it is in
    the parcel: it is declared with its quantity on a line of its own,
    ``netValue`` and ``vatAmount`` 0 (myDATA v2.0.2 §5.4, minimum 0), at
    its own VAT category, and no total changes."""
    invoice = _receipt()
    OrderItemFactory(
        order=invoice.order,
        product=ProductFactory(vat=VatFactory(value=13)),
        price=Money(0, "EUR"),
        quantity=2,
    )
    plain = fromstring(_build(invoice).xml_bytes)

    root = fromstring(_build(invoice, _moving(invoice)).xml_bytes)
    goods, shipping, gift = _details(root)

    assert _text(goods, "quantity") == "3"
    assert _text(shipping, "quantity") is None
    assert _text(gift, "quantity") == "2"
    assert _text(gift, "measurementUnit") == "1"
    assert _text(gift, "netValue") == "0.00"
    assert _text(gift, "vatAmount") == "0.00"
    assert _text(gift, "vatCategory") == "2"  # 13 %
    assert (
        _text(gift, "itemDescr")
        == invoice.order.items.get(price=Money(0, "EUR")).product_name
    )
    for tag in ("totalNetValue", "totalVatAmount", "totalGrossValue"):
        assert _summary(root, tag) == _summary(plain, tag)
    validate_invoice_doc(_build(invoice, _moving(invoice)).xml_bytes)


def test_a_receipt_netted_by_a_refund_declares_what_still_moves():
    """Refunded before the receipt, so its money is one line per rate,
    mixing goods and shipping: each good that still moves gets a line of no
    value with its real quantity, the units bought less those refunded."""
    invoice = _receipt()
    item = invoice.order.items.get()
    type(item).objects.filter(pk=item.pk).update(refunded_quantity=1)
    # Netted by the refunded unit: 37.20 + 5.00 shipping less 12.40.
    invoice.vat_breakdown = [
        {"rate": "24", "subtotal": "24.03", "vat": "5.77", "gross": "29.80"}
    ]
    invoice.total = Money(Decimal("29.80"), "EUR")

    root = fromstring(_build(invoice, _moving(invoice)).xml_bytes)
    money, moving = _details(root)

    assert _text(money, "quantity") is None
    assert _text(money, "netValue") == "24.03"
    assert _text(money, "itemDescr")
    assert _text(moving, "quantity") == "2"
    assert _text(moving, "netValue") == "0.00"
    assert _summary(root, "totalGrossValue") == Decimal("29.80")
    validate_invoice_doc(_build(invoice, _moving(invoice)).xml_bytes)


def test_goods_fully_refunded_do_not_move():
    invoice = _receipt()
    item = invoice.order.items.get()
    type(item).objects.filter(pk=item.pk).update(refunded_quantity=3)

    assert moving_goods(invoice.order) == ()


@pytest.mark.parametrize(
    ("value", "accepted"),
    [
        ("Τατοΐου", True),
        ("", True),
        ("x" * 150, True),
        ("x" * 151, False),
        ("Τατοΐου\x01", False),
        (5, False),
    ],
)
def test_the_dispatch_point_text_is_what_an_aade_document_can_carry(
    value, accepted
):
    """Within AADE's length, and no character XML 1.0 cannot carry: one
    would make every courier receipt an unreadable payload."""
    from order.accounting.movement import validate_loading_text_setting

    assert validate_loading_text_setting(value) is accepted
