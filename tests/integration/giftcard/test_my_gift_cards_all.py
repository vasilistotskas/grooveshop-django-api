"""``GET /api/v1/giftcard/mine/all``: every card of my account, one array.

The account summary totals every card a customer holds. It used to read
them with ``?pagination=false`` on ``/giftcard/mine``, which answered a
bare array where the operation documents the paginated envelope (#684).
This route documents the array.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from giftcard.factories import GiftCardFactory
from giftcard.models import GiftCard
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

URL = reverse("giftcard:giftcard-mine-all")


def _gift_cards(enabled: bool = True):
    values = {"GIFT_CARDS_ENABLED": enabled}
    return patch(
        "extra_settings.models.Setting.get",
        side_effect=lambda key, default=None: values.get(key, default),
    )


def _get(user=None, *, enabled: bool = True):
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    with _gift_cards(enabled):
        return client.get(URL)


def test_it_answers_every_card_of_mine_newest_first():
    user = UserAccountFactory()
    cards = [
        GiftCardFactory(
            code=f"GC-ALLM-INE{n}-CARD",
            issued_to=user,
            recipient_email=user.email,
        )
        for n in range(3)
    ]
    for age, card in enumerate(cards):
        GiftCard.objects.filter(pk=card.pk).update(
            created_at=timezone.now() - timedelta(days=age)
        )
    GiftCardFactory(code="GC-NOTM-INE0-CARD", issued_to=UserAccountFactory())

    response = _get(user)

    assert response.status_code == 200
    assert [row["code"] for row in response.json()] == [
        card.code for card in cards
    ]


def test_it_needs_a_signed_in_customer():
    assert _get().status_code in (401, 403)


def test_it_is_not_found_while_gift_cards_are_off():
    assert _get(UserAccountFactory(), enabled=False).status_code == 404


def test_it_costs_the_same_queries_for_one_card_or_many(
    django_assert_max_num_queries,
):
    """Each balance comes from the list query's ``ledger_total``, not a SUM
    per card."""
    user = UserAccountFactory()
    GiftCardFactory(
        code="GC-ONEC-ARD0-CARD", issued_to=user, recipient_email=user.email
    )
    with CaptureQueriesContext(connection) as one:
        assert _get(user).status_code == 200
    for n in range(4):
        GiftCardFactory(
            code=f"GC-MANY-CAR{n}-CARD",
            issued_to=user,
            recipient_email=user.email,
        )

    with django_assert_max_num_queries(len(one)):
        response = _get(user)

    assert len(response.json()) == 5
