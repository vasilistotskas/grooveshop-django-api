"""The re-review decision must be made on the row as it is under lock.

`submit_profile` deliberately makes its VIES call *outside* the
transaction — a five-second upstream timeout must not hold a row lock.
It then took the lock and decided whether to reset the profile to
PENDING using `previous_status` and `identity_changed` read *before*
that call. Anything a merchant did during the window was invisible.

The docstring's own rule — "SUSPENDED stays SUSPENDED; leaving
suspension is a merchant decision, not a self-service edit" — was
therefore conditional on nobody suspending while a submit was in
flight, which is exactly when a merchant would be doing it.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from b2b.enum import BusinessProfileStatus
from b2b.factories import BusinessProfileFactory

pytestmark = pytest.mark.django_db

PAYLOAD = {
    "company_name": "Renamed Ltd",
    "vat_id": "EL123456783",
    "tax_office": "Α' Αθηνών",
    "activity": "Retail trade",
    "billing_street": "Ermou",
    "billing_street_number": "1",
    "billing_city": "Athens",
    "billing_zipcode": "10563",
}


def _suspend_during_the_vies_call(profile, mock_vies):
    """Make the merchant's suspension land inside the VIES window."""
    original = mock_vies.return_value

    def _suspend_then_answer(*args, **kwargs):
        type(profile).objects.filter(pk=profile.pk).update(
            status=BusinessProfileStatus.SUSPENDED
        )
        return original

    mock_vies.side_effect = _suspend_then_answer


def test_a_suspension_landing_during_the_vies_call_is_not_lifted(
    b2b_tenant, enable_wholesale, mock_vies
):
    profile = BusinessProfileFactory(
        status=BusinessProfileStatus.APPROVED,
        company_name="Original Ltd",
        vat_id="EL123456783",
    )
    _suspend_during_the_vies_call(profile, mock_vies)

    client = APIClient()
    client.force_authenticate(user=profile.user)
    response = client.put(reverse("b2b:b2b-profile"), PAYLOAD, format="json")

    assert response.status_code == status.HTTP_200_OK
    profile.refresh_from_db()
    assert profile.status == BusinessProfileStatus.SUSPENDED, (
        "the customer's own edit lifted a merchant suspension"
    )


def test_an_identity_change_on_an_approved_profile_still_resets_it(
    b2b_tenant, enable_wholesale, mock_vies
):
    """The rule the fix must not break."""
    profile = BusinessProfileFactory(
        status=BusinessProfileStatus.APPROVED,
        company_name="Original Ltd",
        vat_id="EL123456783",
    )

    client = APIClient()
    client.force_authenticate(user=profile.user)
    client.put(reverse("b2b:b2b-profile"), PAYLOAD, format="json")

    profile.refresh_from_db()
    assert profile.status == BusinessProfileStatus.PENDING


def test_an_address_only_edit_keeps_wholesale_access(
    b2b_tenant, enable_wholesale, mock_vies
):
    """The other rule the fix must not break."""
    # ``vat_id`` is stored WITHOUT the country prefix — the serializer
    # strips it — so seeding the payload value verbatim would read as an
    # identity change and reset the profile.
    profile = BusinessProfileFactory(
        status=BusinessProfileStatus.APPROVED,
        company_name=PAYLOAD["company_name"],
        vat_id=PAYLOAD["vat_id"].removeprefix("EL"),
        tax_office=PAYLOAD["tax_office"],
        activity=PAYLOAD["activity"],
    )

    client = APIClient()
    client.force_authenticate(user=profile.user)
    client.put(
        reverse("b2b:b2b-profile"),
        {**PAYLOAD, "billing_street": "Somewhere Else"},
        format="json",
    )

    profile.refresh_from_db()
    assert profile.status == BusinessProfileStatus.APPROVED
