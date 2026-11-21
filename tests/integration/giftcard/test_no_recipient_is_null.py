"""A gift card with no recipient answers ``recipientEmail: null``.

``GET /giftcard/mine`` declared ``recipientEmail`` an email, and a card
issued with no recipient (in the admin, to an account) stores ``""``,
which the storefront's ``z.email()`` refused, so the shopper's whole list
answered 422. The column keeps ``""`` as its one spelling of "none" (NOT
NULL, ``tests/unit/core/test_nullable_string_fields.py``); the API
declares the field nullable and serves ``""`` as ``null``.
"""

from __future__ import annotations

from decimal import Decimal
from unittest import mock

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory
from djmoney.money import Money
from rest_framework.test import APIClient

from giftcard.admin import GiftCardAdmin
from giftcard.enum import GiftCardStatus
from giftcard.models import GiftCard
from giftcard.services import GiftCardService
from tenant.models import TenantMembershipRole
from tests.utils.staff import (
    bind_store_tenant,
    store_staff,
    store_tenant,
    unbind_store_tenant,
)
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

MINE = "/api/v1/giftcard/mine"


def _eur(amount: str) -> Money:
    return Money(Decimal(amount), "EUR")


@pytest.fixture
def tenant():
    store = store_tenant("no_recipient_store", gift_cards_enabled=True)
    previous = bind_store_tenant(store)
    yield store
    unbind_store_tenant(previous)


def _admin_issued(tenant, shopper) -> GiftCard:
    """A card issued through the admin form with no recipient, to
    ``shopper``'s account."""
    owner = store_staff(tenant, TenantMembershipRole.OWNER)
    request = RequestFactory().post("/admin/giftcard/giftcard/")
    request.user = owner
    request.session = {}
    request._messages = FallbackStorage(request)
    admin = GiftCardAdmin(GiftCard, AdminSite())
    form = admin.get_form(request)(
        data={
            "initial_value_0": "30.00",
            "initial_value_1": "EUR",
            "status": GiftCardStatus.ACTIVE,
            "issued_to": shopper.pk,
            "recipient_email": "",
        }
    )
    assert form.is_valid(), form.errors
    card = form.save(commit=False)
    admin.save_model(request, card, form, change=False)
    return GiftCard.objects.get(code=card.code)


def _mine(shopper) -> list[dict]:
    client = APIClient()
    client.force_authenticate(user=shopper)
    settings = {"GIFT_CARDS_ENABLED": True}
    with mock.patch(
        "extra_settings.models.Setting.get",
        side_effect=lambda key, default=None: settings.get(key, default),
    ):
        response = client.get(MINE)
    assert response.status_code == 200, response.content
    return response.json()["results"]


def test_an_admin_issued_card_answers_a_null_recipient(tenant):
    shopper = UserAccountFactory()
    card = _admin_issued(tenant, shopper)

    [row] = _mine(shopper)

    assert card.recipient_email == ""
    assert row["code"] == card.code
    assert row["recipientEmail"] is None


def test_a_purchased_card_answers_its_recipient(tenant):
    shopper = UserAccountFactory()
    card = GiftCardService.issue(
        _eur("25.00"), issued_to=shopper, recipient_email="friend@example.com"
    )

    [row] = _mine(shopper)

    assert row["code"] == card.code
    assert row["recipientEmail"] == "friend@example.com"
