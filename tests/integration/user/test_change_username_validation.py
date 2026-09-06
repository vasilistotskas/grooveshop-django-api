"""`change_username` must hold a username to the same rules as everything else.

The endpoint had no test at all, and its request serializer was a bare
`Serializer` with `CharField(max_length=150)` — five times the model's
`ACCOUNT_USERNAME_MAX_LENGTH` and carrying none of the field's
validators. The view then assigns straight onto the instance and calls
`save(update_fields=["username"])`, which runs no model validation, so
whatever the serializer allowed was written verbatim:

    too long (40):  RAISED DataError: value too long for
                    type character varying(30)      -> 500
    spaces + html:  status=200
       stored: '<script>alert(1)</script>'

The profile serializer has always run allauth's `clean_username`; this
route was the way around it.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

User = get_user_model()


@pytest.fixture
def shopper(db):
    return User.objects.create_user(
        email="shopper@example.gr", password="pw", username="shopper"
    )


@pytest.fixture
def client_as(shopper):
    client = APIClient()
    client.force_authenticate(user=shopper)
    return client


def url_for(user):
    return reverse("user-account-change-username", kwargs={"pk": user.pk})


def test_a_username_longer_than_the_column_is_rejected_not_a_500(
    client_as, shopper
):
    over = "a" * (settings.ACCOUNT_USERNAME_MAX_LENGTH + 10)

    response = client_as.post(
        url_for(shopper), {"username": over}, format="json"
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    shopper.refresh_from_db()
    assert shopper.username == "shopper"


@pytest.mark.parametrize(
    "rejected",
    [
        pytest.param("<script>alert(1)</script>", id="markup"),
        pytest.param("has spaces", id="spaces"),
        pytest.param("semi;colon", id="punctuation"),
    ],
)
def test_characters_the_model_validator_forbids_are_rejected(
    client_as, shopper, rejected
):
    response = client_as.post(
        url_for(shopper), {"username": rejected}, format="json"
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    shopper.refresh_from_db()
    assert shopper.username == "shopper"


def test_a_valid_username_is_still_accepted(client_as, shopper):
    response = client_as.post(
        url_for(shopper), {"username": "new.name-1"}, format="json"
    )

    assert response.status_code == status.HTTP_200_OK
    shopper.refresh_from_db()
    assert shopper.username == "new.name-1"


def test_a_username_someone_else_holds_is_refused(client_as, shopper):
    User.objects.create_user(
        email="other@example.gr", password="pw", username="taken"
    )

    response = client_as.post(
        url_for(shopper), {"username": "taken"}, format="json"
    )

    assert response.status_code in (
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_409_CONFLICT,
    )
    shopper.refresh_from_db()
    assert shopper.username == "shopper"


def test_resubmitting_your_own_username_is_a_no_op_not_a_collision(
    client_as, shopper
):
    """`instance=user` is what keeps the uniqueness check off your own row."""
    response = client_as.post(
        url_for(shopper), {"username": "shopper"}, format="json"
    )

    assert response.status_code == status.HTTP_200_OK
    shopper.refresh_from_db()
    assert shopper.username == "shopper"
