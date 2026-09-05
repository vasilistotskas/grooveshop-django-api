"""`POST /api/v1/user/account` must not be a second registration path.

`UserAccountViewSet` is guarded by `IsOwnerOrAdmin`, whose
`has_permission` only asks whether the caller is authenticated — the
ownership check lives in `has_object_permission`, and a create action
has no object for it to run against. So every logged-in shopper could
POST an account for any address they liked.

The account that produced was not directly loginable (the serializer
declares no `password` field, so the row was written with an empty one),
which is exactly why this stayed unnoticed. The damage is elsewhere:

* **Squatting.** allauth refuses a signup whose email already exists, so
  minting `victim@example.com` locks that person out of registering.
  There is no `EmailAddress` row either, so allauth's own recovery flows
  have nothing to work with.
* **Enumeration.** 201 versus 400 tells the caller whether an address is
  already registered, on a route that needs no more than any session.
* **Relay.** Fields on the row (name, bio, social handles) reach real
  people through notification and marketing mail.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

User = get_user_model()


@pytest.fixture
def shopper(db):
    return User.objects.create_user(
        email="shopper@example.com", password="pw", username="shopper"
    )


@pytest.fixture
def client_as(shopper):
    client = APIClient()
    client.force_authenticate(user=shopper)
    return client


VICTIM = "ceo@victim-company.gr"


def test_a_shopper_cannot_mint_an_account_for_someone_else(client_as):
    response = client_as.post(
        reverse("user-account-list"),
        {"email": VICTIM, "firstName": "Not", "lastName": "Them"},
        format="json",
    )

    assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
    assert not User.objects.filter(email=VICTIM).exists()


def test_the_endpoint_is_not_an_email_enumeration_oracle(client_as, shopper):
    """A registered and an unregistered address must be indistinguishable."""
    registered = client_as.post(
        reverse("user-account-list"), {"email": shopper.email}, format="json"
    )
    unregistered = client_as.post(
        reverse("user-account-list"), {"email": VICTIM}, format="json"
    )

    assert registered.status_code == unregistered.status_code


def test_anonymous_callers_are_refused_too(db):
    response = APIClient().post(
        reverse("user-account-list"), {"email": VICTIM}, format="json"
    )

    assert response.status_code in (
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_405_METHOD_NOT_ALLOWED,
    )
    assert not User.objects.filter(email=VICTIM).exists()


def test_a_shopper_can_still_read_and_update_their_own_account(
    client_as, shopper
):
    """The fix must not touch the routes the storefront actually uses."""
    detail = reverse("user-account-detail", kwargs={"pk": shopper.pk})

    assert client_as.get(detail).status_code == status.HTTP_200_OK

    patched = client_as.patch(detail, {"firstName": "Renamed"}, format="json")
    assert patched.status_code == status.HTTP_200_OK
    shopper.refresh_from_db()
    assert shopper.first_name == "Renamed"


def test_a_profile_patch_cannot_change_the_primary_email(client_as, shopper):
    """Email changes go through allauth so the verification mail is sent."""
    detail = reverse("user-account-detail", kwargs={"pk": shopper.pk})

    response = client_as.patch(detail, {"email": VICTIM}, format="json")

    assert response.status_code == status.HTTP_200_OK
    shopper.refresh_from_db()
    assert shopper.email == "shopper@example.com"
