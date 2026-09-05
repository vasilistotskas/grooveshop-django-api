"""The carrier admin endpoints must answer, not assert.

`StoreStaffModelPermissions` is a `DjangoModelPermissions` subclass, and
DRF's `_queryset(view)` asserts that the view has a `queryset` or a
`get_queryset()` — it needs the model to build the permission codename.
On an `APIView` there was neither, so every AUTHENTICATED caller hit

    AssertionError: Cannot apply StoreStaffModelPermissions on a view
    that does not set `.queryset` or have a `.get_queryset()` method.

and got a 500. An anonymous caller was refused before that, which is why
the endpoints looked like they worked. Four views: BoxNow cancel, ACS
cancel, and both ACS pickup-list views.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

ROUTES = [
    ("shipping-boxnow-cancel", {"parcel_id": "1234567890"}),
    ("shipping-acs-cancel", {"voucher_no": "9001999001"}),
    ("shipping-acs-pickup-list-issue", {}),
]


@pytest.mark.parametrize(("route", "kwargs"), ROUTES)
def test_an_authenticated_caller_gets_an_answer_not_a_500(route, kwargs):
    """403 or 404 are correct answers here; a 500 never is.

    The point is that the permission RESOLVES. Whether this particular
    user is allowed, and whether the parcel exists, are separate
    questions the endpoint is now able to reach.
    """
    client = APIClient()
    client.force_authenticate(user=UserAccountFactory(num_addresses=0))

    response = client.post(reverse(route, kwargs=kwargs))

    assert response.status_code < 500, response.status_code


@pytest.mark.parametrize(("route", "kwargs"), ROUTES)
def test_an_anonymous_caller_is_still_refused(route, kwargs):
    response = APIClient().post(reverse(route, kwargs=kwargs))

    assert response.status_code in (401, 403), response.status_code
