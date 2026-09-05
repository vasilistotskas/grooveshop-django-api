"""OPTIONS must never be a 500, on any route this base class serves.

`ViewSetMixin.initialize_request` sets `action` to "metadata", which is
never a key in `serializers_config` — so the lookup fell through to
`ImproperlyConfigured` and every `BaseModelViewSet` route answered 500
to an authenticated OPTIONS request, across some thirty apps.

Anonymous callers saw a clean 200: DRF's `determine_actions` clones the
request per writable method and checks permissions inside a `try`, so
the permission failure short-circuits before `get_serializer()` — which
sits OUTSIDE that try. That is why CORS preflights never surfaced it.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

ROUTES = [
    "user-address-list",
    "product-list",
    "product-review-list",
    "blog-post-list",
    "cart-list",
]


@pytest.fixture
def signed_in():
    client = APIClient()
    client.force_authenticate(user=UserAccountFactory(num_addresses=0))
    return client


@pytest.mark.parametrize("route", ROUTES)
def test_options_answers_for_a_signed_in_caller(signed_in, route):
    response = signed_in.options(reverse(route))

    assert response.status_code == 200, response.status_code


@pytest.mark.parametrize("route", ROUTES)
def test_options_is_never_a_server_error_anonymously(db, route):
    """401 is a correct answer here; 5xx is never one.

    A route gated by `IsAuthenticated` refuses an anonymous OPTIONS on
    the request itself, before the metadata machinery runs — that is
    DRF working, not the defect.
    """
    response = APIClient().options(reverse(route))

    assert response.status_code < 500, response.status_code


def test_the_metadata_describes_the_write_serializer(signed_in):
    """OPTIONS exists to say what you may send, so it must say it."""
    response = signed_in.options(reverse("user-address-list"))

    actions = response.data.get("actions", {})
    assert "POST" in actions, response.data
    assert actions["POST"], "no field metadata for POST"
