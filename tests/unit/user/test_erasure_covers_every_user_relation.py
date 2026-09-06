"""Every relation to `UserAccount` must have a decided erasure outcome.

Right-to-erasure is the one place where forgetting a model is silently
wrong: `SET_NULL` makes the row *look* erased — the FK really is gone —
while every denormalised copy of the subject's data on that row stays
exactly where it was.  The erasure ran, logged "GDPR deletion complete",
returned a tally, and left the person's search history, IP addresses and
email addresses on disk.

`CASCADE` needs no attention: the row goes with the account.  Everything
else is a decision, and this test requires that the decision was actually
taken — either the model is handled in `anonymise_and_delete_user`, or it
is named below with the reason it does not need to be.

Adding a model with a non-cascading FK to `UserAccount` fails this test
until one of those two things is true.
"""

from __future__ import annotations

import ast
import inspect

import pytest
from django.contrib.auth import get_user_model
from django.db import models

from user.services import gdpr

User = get_user_model()

# Relations that are deliberately left alone, each with the reason.
# A "staff FK" records which operator performed an action; the row is
# about the operator's *act*, not about the subject, and severing the FK
# is the whole of the personal data on it.
_NOT_SUBJECT_DATA = {
    "core.CachePurgeLog.actor": "staff FK — records who purged a cache",
    "product.Product.changed_by": "staff FK — the row is product data",
    "loyalty.PointsTransaction.created_by": "staff FK — operator who granted",
    "giftcard.GiftCardTransaction.created_by": (
        "staff FK — the operator who adjusted a card's balance; the "
        "cardholder is GiftCard.issued_to, which is handled"
    ),
    "b2b.BusinessProfile.reviewed_by": "staff FK — reviewer of a company",
    "shipping_acs.AcsPickupList.issued_by": (
        "staff FK — the operator who closed a pickup list; recipient "
        "details live on the shipment, scrubbed via the order"
    ),
    "order.OrderItemHistory.user": (
        "carries only previous_value/new_value for an item change; no "
        "request metadata and no copy of the subject's details"
    ),
    "order.StockLog.performed_by": (
        "stock movement audit; the row holds quantities, not identity"
    ),
    "order.StockReservation.reserved_by": (
        "15-minute TTL rows reaped by cleanup_expired_reservations; the "
        "session_id is an ephemeral token, not a stored identifier"
    ),
    "blog.BlogComment.user": (
        "published user-generated content. Orphaning the comment keeps "
        "the thread readable and removes the authorship, which is the "
        "conventional treatment; deleting it would silently edit other "
        "people's conversations"
    ),
    "djstripe.Customer.subscriber": (
        "dj-stripe's mirror of a Stripe-side record. Erasing it here "
        "would desync the mirror without erasing anything at Stripe, "
        "where the deletion actually has to be requested"
    ),
}


def _handled_model_names() -> frozenset[str]:
    """Model names the erasure function names for itself.

    Read out of the function's own source rather than from a hand-kept
    list, so the two cannot drift: a model stops counting as handled the
    moment its queryset is removed.
    """
    tree = ast.parse(inspect.getsource(gdpr.anonymise_and_delete_user))
    names = set()
    for node in ast.walk(tree):
        match node:
            case ast.Attribute(value=ast.Name(id=name), attr="objects"):
                names.add(name)
    return frozenset(names)


def test_every_non_cascading_relation_to_a_user_is_handled_or_exempt():
    handled = _handled_model_names()
    assert handled, "Parsed no querysets — the detector is broken."

    undecided = []
    for rel in User._meta.related_objects:
        if rel.many_to_many:
            # The through row cascades with the account.
            continue
        if rel.field.remote_field.on_delete is models.CASCADE:
            continue

        label = f"{rel.related_model._meta.label}.{rel.field.name}"
        if rel.related_model.__name__ in handled or label in _NOT_SUBJECT_DATA:
            continue
        undecided.append(label)

    assert not undecided, (
        "These relations survive account deletion with no decision "
        "recorded. Either handle the model in "
        "`anonymise_and_delete_user`, or add it to `_NOT_SUBJECT_DATA` "
        "with the reason it holds nothing about the subject:\n  "
        + "\n  ".join(sorted(undecided))
    )


def test_no_exemption_outlives_its_reason():
    """An exemption for a relation that no longer exists must be removed."""
    live = {
        f"{rel.related_model._meta.label}.{rel.field.name}"
        for rel in User._meta.related_objects
        if not rel.many_to_many
    }
    stale = sorted(set(_NOT_SUBJECT_DATA) - live)

    assert not stale, (
        "These relations are gone; drop their exemptions:\n  "
        + "\n  ".join(stale)
    )


@pytest.mark.parametrize(("label", "reason"), sorted(_NOT_SUBJECT_DATA.items()))
def test_every_exemption_states_a_reason(label, reason):
    assert len(reason) > 20, f"{label} needs a real reason, not '{reason}'"
