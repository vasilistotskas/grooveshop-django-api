"""GDPR erasure and export read one list of an order's address fields.

``Order.SHIPPING_ADDRESS_FIELDS`` is what ``anonymise_and_delete_user``
blanks on a retained order and what a data export lists. An address field
added to ``Order`` without joining it would survive an erasure (it happened
to ``address_line_2``, #641). The staging anonymiser already has to name
every personal field (``devtools.anonymise.rules``), so this test holds the
erasure to the same set: every field the anonymiser scrubs on an order is
erased, or kept for a stated reason.
"""

from __future__ import annotations

from devtools.anonymise.rules import REGISTRY
from order.models.order import Order

# Personal fields on an order that erasure keeps, and why.
KEPT = {
    # The identity erasure replaces with placeholders, not blanks.
    "email": "replaced with a placeholder address",
    "first_name": "replaced with a placeholder name",
    "last_name": "replaced with a placeholder name",
    "phone": "blanked with the address",
    "customer_notes": "blanked with the address",
    # The invoice's counterparty, kept with the invoice (tax law).
    "billing_vat_id": "kept with the issued invoice",
    "billing_company_name": "kept with the issued invoice",
    "billing_tax_office": "kept with the issued invoice",
    "billing_activity": "kept with the issued invoice",
    "billing_street": "kept with the issued invoice",
    "billing_street_number": "kept with the issued invoice",
    "billing_city": "kept with the issued invoice",
    "billing_zipcode": "kept with the issued invoice",
    # JSON columns: no address of the buyer, but ``metadata`` holds their
    # browser identity (``meta``) and a typed cancel reason, which this
    # erasure does not clear yet. Clearing them, and every carrier copy,
    # is its own change ("erasure reaches every copy of a subject's data").
    "metadata": "no address; its personal keys are not erased yet",
    "private_metadata": "no address; nothing writes it on an order",
    "uuid": "the order's identifier",
}


def test_every_scrubbed_order_field_is_erased_or_kept_on_purpose():
    scrubbed = set(REGISTRY["order.Order"].fields)

    unaccounted = scrubbed - set(Order.SHIPPING_ADDRESS_FIELDS) - set(KEPT)

    assert not unaccounted, (
        f"Add {sorted(unaccounted)} to Order.SHIPPING_ADDRESS_FIELDS, or to "
        "KEPT here with the reason it survives an erasure."
    )


def test_every_listed_field_is_a_field_of_the_order():
    names = {field.name for field in Order._meta.get_fields()}

    assert set(Order.SHIPPING_ADDRESS_FIELDS) <= names
