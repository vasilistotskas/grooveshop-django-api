"""Unit tests for the ``send_price_drop_notifications`` Celery task.

The earlier audit flagged this task as "never triggered" — that was
wrong (it fires from the price-history signal in ``product/signals.py``
on Product price drops). What was actually missing is happy-path test
coverage that the favourite fan-out actually creates Notification rows.
This file adds that coverage.
"""

from __future__ import annotations

import pytest

from notification.models.user import NotificationUser
from product.factories.product import ProductFactory
from product.models.favourite import ProductFavourite
from product.tasks import send_price_drop_notifications
from user.factories.account import UserAccountFactory


@pytest.mark.django_db
class TestSendPriceDropNotifications:
    """Cover the favourite fan-out and the early-return guards."""

    def test_creates_notification_for_each_favouriting_user(self):
        product = ProductFactory(active=True, num_images=0, num_reviews=0)
        favouriting_users = [
            UserAccountFactory(num_addresses=0) for _ in range(3)
        ]
        non_favouriting_user = UserAccountFactory(num_addresses=0)
        for user in favouriting_users:
            ProductFavourite.objects.create(user=user, product=product)

        result = send_price_drop_notifications(
            product_id=product.id, old_price=20.0, new_price=15.0
        )

        assert result["status"] == "success"
        assert result["notified"] == 3

        # Each favouriting user has a notification linked to them.
        notified_users = set(
            NotificationUser.objects.filter(
                user__in=favouriting_users
            ).values_list("user_id", flat=True)
        )
        assert notified_users == {u.id for u in favouriting_users}

        # The non-favouriting user has no notification — the fan-out is
        # scoped to ProductFavourite rows.
        assert not NotificationUser.objects.filter(
            user=non_favouriting_user
        ).exists()

    def test_skips_when_product_not_found(self):
        result = send_price_drop_notifications(
            product_id=999_999, old_price=20.0, new_price=15.0
        )
        assert result == {
            "status": "skipped",
            "reason": "product_not_found",
        }

    def test_zero_favourites_returns_zero_notified(self):
        product = ProductFactory(active=True, num_images=0, num_reviews=0)

        result = send_price_drop_notifications(
            product_id=product.id, old_price=20.0, new_price=15.0
        )

        assert result["status"] == "success"
        assert result["notified"] == 0
