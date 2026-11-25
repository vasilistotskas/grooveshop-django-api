"""Deleting an account asks for a recent reauthentication.

allauth up to 65.21 counted a customer who had no way to reauthenticate (no
password, no second factor) as always freshly reauthenticated; 65.22 refuses
such a customer. While the code method is off they have no way to be asked,
so the check is not asked of them
(``TenantAccountAdapter.is_reauthentication_required``); everyone else is.
"""

from __future__ import annotations

import pytest
from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.internal.flows.reauthentication import (
    is_reauthentication_required,
)
from allauth.core.context import request_context
from allauth.mfa.models import Authenticator
from allauth.mfa.totp.internal.auth import generate_totp_secret
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

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


def test_a_customer_with_no_way_to_reauthenticate_keeps_the_deletion(
    client_and_user,
):
    client, user = client_and_user
    assert not user.has_usable_password()

    response = _delete(client, user)

    assert response.status_code == status.HTTP_202_ACCEPTED


def test_a_customer_with_a_password_must_reauthenticate_first(client_and_user):
    client, user = client_and_user
    user.set_password("S3cure-pass-phrase!")
    user.save(update_fields=["password"])

    response = _delete(client, user)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["code"] == "reauthentication_required"
    assert User.objects.filter(pk=user.pk).exists()


def test_a_customer_with_a_second_factor_must_reauthenticate_first(
    client_and_user,
):
    client, user = client_and_user
    Authenticator.objects.create(
        user=user,
        type=Authenticator.Type.TOTP,
        data={"secret": generate_totp_secret()},
    )

    response = _delete(client, user)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["code"] == "reauthentication_required"


@pytest.mark.parametrize(
    "app", ["allauth.mfa", "allauth.socialaccount", "allauth.account", "user"]
)
@pytest.mark.parametrize("password", [None, "S3cure-pass-phrase!"])
def test_only_a_customer_with_a_way_to_reauthenticate_is_asked(
    app, password, rf
):
    request = rf.post("/")
    request.session = {}
    request.user = User.objects.create_user(
        email="asked@example.gr", password=password, username="asked"
    )

    with request_context(request):
        asked = is_reauthentication_required(request, app=app)

    # Without our rule allauth asks where its app says so.
    assert asked == (password is not None and _app_asks(app))


def _app_asks(app: str) -> bool:
    return DefaultAccountAdapter().is_reauthentication_required(app=app)
