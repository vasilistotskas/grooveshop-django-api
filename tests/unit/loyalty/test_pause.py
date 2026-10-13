"""The time loyalty was off does not count against points.

``LoyaltyPause`` records each span the programme was off, and a point's
age for expiry counts only the time it was on: shoppers cannot spend
points while it is off, so that time must not eat into them.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from loyalty.enum import TransactionType
from loyalty.models import LoyaltyPause, PointsTransaction
from loyalty.services import LoyaltyService
from tests.utils.loyalty import loyalty_settings
from tests.utils.store_settings import switch_store_settings
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

YEAR = timedelta(days=365)


class TestActiveCutoff:
    def test_without_pauses_it_is_plain_age(self):
        now = timezone.now()

        assert LoyaltyPause.active_cutoff(now, YEAR) == now - YEAR

    def test_a_pause_inside_the_window_pushes_it_back_by_its_length(self):
        now = timezone.now()
        LoyaltyPause.objects.create(
            started_at=now - timedelta(days=100),
            ended_at=now - timedelta(days=70),
        )

        assert LoyaltyPause.active_cutoff(now, YEAR) == (
            now - YEAR - timedelta(days=30)
        )

    def test_an_open_pause_counts_up_to_now(self):
        now = timezone.now()
        LoyaltyPause.objects.create(started_at=now - timedelta(days=10))

        assert LoyaltyPause.active_cutoff(now, timedelta(days=5)) == (
            now - timedelta(days=15)
        )

    def test_a_pause_older_than_the_window_changes_nothing(self):
        now = timezone.now()
        LoyaltyPause.objects.create(
            started_at=now - timedelta(days=400),
            ended_at=now - timedelta(days=370),
        )

        assert LoyaltyPause.active_cutoff(now, timedelta(days=30)) == (
            now - timedelta(days=30)
        )


class TestSyncPause:
    def test_switching_off_opens_one_span_and_back_on_closes_it(self):
        with loyalty_settings(LOYALTY_ENABLED=False):
            LoyaltyService.sync_pause()
            LoyaltyService.sync_pause()

        assert LoyaltyPause.objects.filter(ended_at__isnull=True).count() == 1

        with loyalty_settings(LOYALTY_ENABLED=True):
            LoyaltyService.sync_pause()

        assert not LoyaltyPause.objects.filter(ended_at__isnull=True).exists()


def test_a_point_does_not_age_while_the_programme_was_off():
    """Earned 380 days ago with a 30-day pause since: 350 days of live
    age, so a 365-day expiry has not reached it yet."""
    now = timezone.now()
    user = UserAccountFactory(num_addresses=0)
    earned = PointsTransaction.objects.create(
        user=user,
        points=20,
        transaction_type=TransactionType.EARN,
        description="Earned before the pause",
    )
    PointsTransaction.objects.filter(pk=earned.pk).update(
        created_at=now - timedelta(days=380)
    )
    LoyaltyPause.objects.create(
        started_at=now - timedelta(days=200),
        ended_at=now - timedelta(days=170),
    )

    with loyalty_settings(LOYALTY_POINTS_EXPIRATION_DAYS=365):
        created = LoyaltyService.process_expiration()

    assert created == 0
    assert LoyaltyService.get_user_balance(user) == 20


def test_the_store_switch_queues_the_pause_sync(bind_tenant_and_schema):
    from types import SimpleNamespace

    from loyalty.tasks import sync_loyalty_pause

    bind_tenant_and_schema(SimpleNamespace(schema_name="shop_one"))

    with patch("tenant.switches.dispatch_on_commit") as dispatch:
        switch_store_settings(LOYALTY_ENABLED=True)

    dispatch.assert_called_once_with(sync_loyalty_pause, schema_name="shop_one")
