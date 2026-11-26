"""Deleting an account asks for a recent reauthentication, always.

allauth up to 65.21 counted a customer who had no way to reauthenticate (no
password, no second factor) as always freshly reauthenticated, so the
erasure of their account went through unasked. From 65.22 such a customer is
refused: a way to prove who they are is a password, a code to a verified
address or a second factor (``ACCOUNT_REAUTHENTICATION_METHODS``).

The storefront calls this API with a Knox token, which carries no session,
and confirms it is the customer through allauth's app API, whose session it
names with ``X-Session-Token``: the view reads the reauthentication there.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from allauth.account.internal.flows.login import record_authentication
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.http import HttpRequest
from django.test import Client
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from user.factories.account import UserAccountFactory

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture
def client_and_user():
    user = User.objects.create_user(
        email="subject@example.gr", password=None, username="subject"
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


def _delete(client, user):
    return client.post(
        reverse("user-account-delete-account", args=[user.pk]),
        {"confirmation": "DELETE"},
        format="json",
    )


def test_a_customer_with_no_way_to_reauthenticate_cannot_delete_the_account(
    client_and_user,
):
    client, user = client_and_user
    assert not user.has_usable_password()
    assert not EmailAddress.objects.filter(user=user, verified=True).exists()

    response = _delete(client, user)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["code"] == "reauthentication_required"
    assert User.objects.filter(pk=user.pk).exists()


def test_a_customer_with_a_verified_address_must_reauthenticate_first(
    client_and_user,
):
    client, user = client_and_user
    EmailAddress.objects.create(
        user=user, email=user.email, verified=True, primary=True
    )

    response = _delete(client, user)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["code"] == "reauthentication_required"


ALLAUTH = "/_allauth/app/v1"
PASSWORD = "S3cure-pass-phrase!"


def _allauth(path: str, body: dict, token: str | None = None):
    headers = {"HTTP_X_SESSION_TOKEN": token} if token else {}
    return Client().post(
        f"{ALLAUTH}{path}",
        data=json.dumps(body),
        content_type="application/json",
        **headers,
    )


@pytest.fixture
def signed_in():
    """A customer with a password, signed in to allauth's app API: the
    session token the storefront holds."""
    user = UserAccountFactory(plain_password=PASSWORD)
    EmailAddress.objects.create(
        user=user, email=user.email, verified=True, primary=True
    )
    response = _allauth(
        "/auth/login", {"email": user.email, "password": PASSWORD}
    )
    assert response.status_code == status.HTTP_200_OK
    client = APIClient()
    client.force_authenticate(user=user)
    return SimpleNamespace(
        user=user, client=client, token=response.json()["meta"]["session_token"]
    )


def _delete_with(signed, token):
    return signed.client.post(
        reverse("user-account-delete-account", args=[signed.user.pk]),
        {"confirmation": "DELETE"},
        format="json",
        HTTP_X_SESSION_TOKEN=token,
    )


class TestTheStorefrontsSession:
    def test_a_customer_who_confirmed_it_is_them_deletes_the_account(
        self, signed_in
    ):
        confirmed = _allauth(
            "/auth/reauthenticate", {"password": PASSWORD}, signed_in.token
        )
        assert confirmed.status_code == status.HTTP_200_OK

        response = _delete_with(signed_in, signed_in.token)

        assert response.status_code == status.HTTP_202_ACCEPTED

    def test_one_who_did_not_is_told_how_they_can(self, signed_in):
        response = _delete_with(signed_in, signed_in.token)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json()["code"] == "reauthentication_required"
        # The code mailed to the customer's verified address is a way too;
        # only the declared fields reach the client.
        assert response.json()["flows"] == [
            {"id": "reauthenticate"},
            {"id": "reauthenticate_by_code"},
        ]

    def test_another_customers_session_proves_nothing(self, signed_in):
        other = UserAccountFactory(plain_password=PASSWORD)
        EmailAddress.objects.create(
            user=other, email=other.email, verified=True, primary=True
        )
        login = _allauth(
            "/auth/login", {"email": other.email, "password": PASSWORD}
        )
        assert login.status_code == status.HTTP_200_OK
        theirs = login.json()["meta"]["session_token"]
        confirmed = _allauth(
            "/auth/reauthenticate", {"password": PASSWORD}, theirs
        )
        assert confirmed.status_code == status.HTTP_200_OK

        response = _delete_with(signed_in, theirs)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json()["code"] == "reauthentication_required"

    def test_another_customers_cookie_session_proves_nothing(self, signed_in):
        # The customer's token sent with a cookie session in which someone
        # else confirmed it is them just now.
        other = UserAccountFactory(plain_password=PASSWORD)
        session = signed_in.client.session
        borrowed = HttpRequest()
        borrowed.session = session
        borrowed.user = other
        record_authentication(
            borrowed, other, method="password", reauthenticated=True
        )
        session.save()

        response = signed_in.client.post(
            reverse("user-account-delete-account", args=[signed_in.user.pk]),
            {"confirmation": "DELETE"},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json()["code"] == "reauthentication_required"
