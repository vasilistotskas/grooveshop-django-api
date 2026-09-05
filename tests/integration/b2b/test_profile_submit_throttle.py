"""The B2B submit budget must actually bind a signed-in caller.

`submit_profile` is `IsAuthenticated`, and `B2BProfileSubmitThrottle`
was an `AnonRateThrottle` — whose `get_cache_key` returns `None` for an
authenticated request. DRF also checks permissions *before* throttles,
so an anonymous caller was turned away before the throttle ran at all.
Between them, the `5/minute` budget on `b2b_profile_submit` could never
apply to anybody, on an endpoint whose own docstring calls each submit
"a request amplifier against both our workers and VIES" because it makes
an outbound VIES call with a 5-second timeout.
"""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.api.throttling import B2BProfileSubmitThrottle
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

PAYLOAD = {
    "company_name": "Example IKE",
    "vat_id": "EL123456783",
    "tax_office": "Α' Αθηνών",
    "activity": "Retail trade",
    "billing_street": "Ermou",
    "billing_street_number": "1",
    "billing_city": "Athens",
    "billing_zipcode": "10563",
}
RATE = 5


@pytest.fixture
def throttled_rate(monkeypatch):
    """Give the throttle a rate; the suite runs with them all disabled.

    Set on the class rather than through the ``settings`` fixture.
    ``SimpleRateThrottle.THROTTLE_RATES`` is bound to the settings dict
    **at import time**, and every rate resolves to ``None`` under
    ``DEBUG`` — which is what settings.py asks for and what the suite
    runs under. Overriding ``REST_FRAMEWORK`` afterwards therefore
    reaches the throttle only depending on what else has already
    touched ``api_settings``, which made this test pass alone and fail
    in a full run. ``rate`` is the documented per-class escape hatch:
    ``__init__`` uses it and skips ``get_rate()`` entirely.
    """
    monkeypatch.setattr(
        B2BProfileSubmitThrottle, "rate", f"{RATE}/minute", raising=False
    )
    cache.clear()
    yield
    cache.clear()


def test_a_signed_in_caller_runs_out_of_submits(
    b2b_tenant, enable_wholesale, throttled_rate, mock_vies
):
    client = APIClient()
    client.force_authenticate(user=UserAccountFactory())

    statuses = [
        client.put(
            reverse("b2b:b2b-profile"), PAYLOAD, format="json"
        ).status_code
        for _ in range(RATE + 2)
    ]

    assert status.HTTP_429_TOO_MANY_REQUESTS in statuses, (
        f"The 5/minute budget never bound an authenticated caller. "
        f"Observed: {statuses}"
    )
    assert statuses.count(status.HTTP_429_TOO_MANY_REQUESTS) == 2


def test_two_callers_do_not_share_one_budget(
    b2b_tenant, enable_wholesale, throttled_rate, mock_vies
):
    """Keyed by user id: exhausting your own must not lock out a colleague.

    Under the previous per-IP key this was the trade-off; under the new
    one it is not, and two people behind one office address are
    independent.
    """
    first, second = APIClient(), APIClient()
    first.force_authenticate(user=UserAccountFactory())
    second.force_authenticate(user=UserAccountFactory())

    for _ in range(RATE + 2):
        first.put(reverse("b2b:b2b-profile"), PAYLOAD, format="json")
    response = second.put(reverse("b2b:b2b-profile"), PAYLOAD, format="json")

    assert response.status_code != status.HTTP_429_TOO_MANY_REQUESTS
