"""A gift card with no recipient has a NULL ``recipient_email``.

``GET /giftcard/mine`` declares ``recipientEmail`` an email, so the empty
string an admin-issued card used to store made the storefront's
``z.email()`` refuse the shopper's whole list. Every writer now stores
NULL, the API serves null (declared nullable), and the migration turned
the old empty strings into NULL.
"""

from __future__ import annotations

import importlib
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.apps import apps
from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory
from django.utils import timezone
from djmoney.money import Money

from giftcard.admin import GiftCardAdmin
from giftcard.enum import GiftCardStatus
from giftcard.models import GiftCard
from giftcard.serializers import GiftCardSerializer
from giftcard.services import GiftCardService
from giftcard.tasks import deliver_scheduled_gift_cards
from tenant.models import TenantMembershipRole
from tests.utils.openapi import openapi_document, validate_against
from tests.utils.staff import (
    bind_store_tenant,
    store_staff,
    store_tenant,
    unbind_store_tenant,
)

pytestmark = pytest.mark.django_db

MIGRATION = "giftcard.migrations.0011_giftcard_recipient_email_null"


def _eur(amount: str) -> Money:
    return Money(Decimal(amount), "EUR")


@pytest.fixture
def tenant():
    store = store_tenant("no_recipient_store", gift_cards_enabled=True)
    previous = bind_store_tenant(store)
    yield store
    unbind_store_tenant(previous)


def test_a_card_issued_without_a_recipient_has_none():
    card = GiftCardService.issue(_eur("20.00"))

    card.refresh_from_db()
    assert card.recipient_email is None


def test_the_admin_stores_an_empty_recipient_as_none(tenant):
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
            "recipient_email": "",
        }
    )

    assert form.is_valid(), form.errors
    card = form.save(commit=False)
    admin.save_model(request, card, form, change=False)

    assert GiftCard.objects.get(code=card.code).recipient_email is None


def test_the_api_serves_it_as_null_and_the_schema_declares_null():
    card = GiftCardService.issue(_eur("20.00"))
    document = openapi_document()

    data = GiftCardSerializer(card).data

    assert data["recipient_email"] is None
    validate_against(
        document,
        document["components"]["schemas"]["GiftCard"]["properties"][
            "recipientEmail"
        ],
        None,
    )


def test_the_delivery_sweep_skips_a_card_with_no_recipient():
    GiftCard.objects.create(
        code="TESTNORECIPIENT01",
        initial_value=_eur("50.00"),
        status=GiftCardStatus.ACTIVE,
        deliver_at=timezone.now() - timedelta(hours=1),
    )

    with (
        patch(
            "giftcard.services.GiftCardService.is_enabled", return_value=True
        ),
        patch("giftcard.tasks.deliver_gift_card_email") as task,
    ):
        result = deliver_scheduled_gift_cards()

    task.delay.assert_not_called()
    assert result == {"status": "success", "sent": 0}


class TestTheMigration:
    def _card(self, code: str, email: str | None) -> GiftCard:
        card = GiftCard.objects.create(code=code, initial_value=_eur("10"))
        GiftCard.objects.filter(pk=card.pk).update(recipient_email=email)
        return card

    def test_it_turns_every_empty_recipient_into_null_and_back(self):
        module = importlib.import_module(MIGRATION)
        empty = self._card("TESTEMPTY0000001", "")
        named = self._card("TESTNAMED0000001", "friend@example.com")

        module.empty_to_null(apps, None)

        empty.refresh_from_db()
        named.refresh_from_db()
        assert empty.recipient_email is None
        assert named.recipient_email == "friend@example.com"

        module.null_to_empty(apps, None)

        empty.refresh_from_db()
        assert empty.recipient_email == ""
