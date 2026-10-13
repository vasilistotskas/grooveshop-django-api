"""The time gift cards were off does not count against a card.

``GiftCardPause`` records each span the store had gift cards off. When
they come back on, every active card's expiry moves forward by the span,
so a card loses no validity while it could not be spent.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.utils import timezone

from giftcard.enum import GiftCardStatus
from giftcard.factories import GiftCardFactory
from giftcard.models import GiftCardPause
from giftcard.services import GiftCardService
from giftcard.tasks import sync_gift_card_pause
from tests.utils.store_settings import switch_store_settings

pytestmark = pytest.mark.django_db

SETTING_GET = "giftcard.services.Setting.get"


def _gift_cards(on: bool):
    return patch(
        SETTING_GET,
        side_effect=lambda key, default=None: (
            on if key == "GIFT_CARDS_ENABLED" else default
        ),
    )


def test_switching_off_opens_one_span():
    with _gift_cards(False):
        GiftCardService.sync_pause()
        GiftCardService.sync_pause()

    assert GiftCardPause.objects.filter(ended_at__isnull=True).count() == 1


def test_back_on_moves_each_active_card_forward_by_the_off_time():
    now = timezone.now()
    GiftCardPause.objects.create(started_at=now - timedelta(days=10))
    # Its date passed while gift cards were off.
    lapsed = GiftCardFactory(expires_at=now - timedelta(days=2))
    later = GiftCardFactory(expires_at=now + timedelta(days=300))

    with _gift_cards(True):
        GiftCardService.sync_pause()

    lapsed.refresh_from_db()
    later.refresh_from_db()
    off = timedelta(days=10)
    tolerance = timedelta(minutes=1)
    assert abs(lapsed.expires_at - (now - timedelta(days=2) + off)) < tolerance
    assert abs(later.expires_at - (now + timedelta(days=300) + off)) < tolerance
    assert not GiftCardPause.objects.filter(ended_at__isnull=True).exists()


def test_a_card_expired_before_the_pause_or_not_active_stays_put():
    now = timezone.now()
    GiftCardPause.objects.create(started_at=now - timedelta(days=10))
    before = now - timedelta(days=20)
    gone = GiftCardFactory(expires_at=before)
    disabled = GiftCardFactory(
        status=GiftCardStatus.DISABLED, expires_at=now + timedelta(days=5)
    )

    with _gift_cards(True):
        GiftCardService.sync_pause()

    gone.refresh_from_db()
    disabled.refresh_from_db()
    assert gone.expires_at == before
    assert abs(disabled.expires_at - (now + timedelta(days=5))) < timedelta(
        seconds=5
    )


def test_a_card_whose_date_passed_while_off_is_not_expired_after():
    """The daily sweep after re-enabling closes the span first, so the
    card it would have zeroed has its validity back."""
    now = timezone.now()
    GiftCardPause.objects.create(started_at=now - timedelta(days=10))
    card = GiftCardFactory(expires_at=now - timedelta(days=2))

    with _gift_cards(True):
        expired = GiftCardService.expire_cards()

    assert expired == 0
    card.refresh_from_db()
    assert card.balance.amount > 0


def test_the_store_switch_queues_the_pause_sync(bind_tenant_and_schema):
    bind_tenant_and_schema(SimpleNamespace(schema_name="shop_one"))

    with patch("tenant.switches.dispatch_on_commit") as dispatch:
        switch_store_settings(GIFT_CARDS_ENABLED=True)

    dispatch.assert_called_once_with(
        sync_gift_card_pause, schema_name="shop_one"
    )
