"""Deleting an account asks for a recent reauthentication, always.

allauth up to 65.21 counted a customer who had no way to reauthenticate (no
password, no second factor) as always freshly reauthenticated, so the
erasure of their account went through unasked. From 65.22 such a customer is
refused: a way to prove who they are is a password, a code to a verified
address or a second factor (``ACCOUNT_REAUTHENTICATION_METHODS``).
"""

from __future__ import annotations

import pytest
from allauth.account.models import EmailAddress
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
