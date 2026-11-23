"""GDPR erasure and export read one definition of an order's personal data.

``Order.PERSONAL_FIELDS`` and ``Order.PERSONAL_METADATA`` are what
``order.privacy`` clears on a retained order (the billing fields of an
issued invoice excepted) and what a data export lists. A personal field
added to ``Order`` without joining them would survive an erasure. The
staging anonymiser already has to name every personal field
(``devtools.anonymise.rules``), so this test holds the definition to the
same set.

The JSON columns hold personal data at some paths only, and copies of it
land in other tables. Neither is exempt:
``tests/integration/user/test_gdpr_erasure_completeness.py``
(``test_no_value_of_the_subject_survives_on_what_erasure_keeps``) erases a
subject whose orders went through the code that copies their data (a
cancel with a typed reason, a refund, a restock, the carriers' records)
and searches every text and JSON column of every table for it.
"""

from __future__ import annotations

from devtools.anonymise.rules import REGISTRY
from order.models.order import Order

# Scrubbed by the anonymiser, but not columns of the buyer's own data.
NOT_PERSONAL_COLUMNS = {
    "metadata": "personal at Order.PERSONAL_METADATA only; swept for the "
    "subject's values by the erasure completeness test",
    "private_metadata": "nothing in the code writes it on an order; swept "
    "for the subject's values by the erasure completeness test",
    "uuid": "the order's identifier",
}


def test_every_scrubbed_order_field_is_personal_or_not_on_purpose():
    scrubbed = set(REGISTRY["order.Order"].fields)

    unaccounted = (
        scrubbed - set(Order.PERSONAL_FIELDS) - set(NOT_PERSONAL_COLUMNS)
    )

    assert not unaccounted, (
        f"Add {sorted(unaccounted)} to Order.PERSONAL_FIELDS, or to "
        "NOT_PERSONAL_COLUMNS here with the reason it is not personal."
    )


def test_every_listed_field_is_a_field_of_the_order():
    names = {field.name for field in Order._meta.get_fields()}

    assert set(Order.PERSONAL_FIELDS) <= names
    assert set(Order.BILLING_FIELDS) <= set(Order.PERSONAL_FIELDS)
