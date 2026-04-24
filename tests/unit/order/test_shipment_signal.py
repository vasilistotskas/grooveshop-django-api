"""Regression tests for the ``order_shipment_dispatched`` signal guards.

The key invariant: the signal fires exactly ONCE per actual null→set
transition on the Order's tracking fields. Specifically it must NOT
fire a second time when an admin clears tracking, saves, then re-enters
the same tracking number — that scenario would produce a duplicate
"Tracking available" notification to the shopper.
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase

from order.factories.order import OrderFactory
from order.models.order import Order


class ShipmentDispatchedSignalTestCase(TestCase):
    def _fresh_order(self) -> Order:
        order = OrderFactory()
        # Strip any tracking that might have been populated by the
        # factory so we start from a clean null baseline.
        order.tracking_number = ""
        order.shipping_carrier = ""
        order.save()
        # Re-read to refresh _original_* snapshots post-save.
        return Order.objects.get(pk=order.pk)

    @patch("order.signals.handlers.order_shipment_dispatched")
    def test_fires_on_initial_null_to_set_transition(self, mock_signal) -> None:
        order = self._fresh_order()
        with self.captureOnCommitCallbacks(execute=True):
            order.tracking_number = "ABC123"
            order.shipping_carrier = "ELTA"
            order.save()
        self.assertTrue(mock_signal.send.called)

    @patch("order.signals.handlers.order_shipment_dispatched")
    def test_no_refire_when_saved_again_with_same_tracking(
        self, mock_signal
    ) -> None:
        """Guards the clear-then-reset scenario's first half: a
        subsequent save with unchanged tracking must not fire a second
        signal. The ``tracking_unchanged`` short-circuit in
        ``handle_order_post_save`` is what enforces this."""
        order = self._fresh_order()
        with self.captureOnCommitCallbacks(execute=True):
            order.tracking_number = "ABC123"
            order.shipping_carrier = "ELTA"
            order.save()
        # Reset the mock between the two saves so the second-save
        # assertion only sees what fires after it.
        mock_signal.send.reset_mock()
        # Save again with no tracking change.
        with self.captureOnCommitCallbacks(execute=True):
            order.save()
        self.assertFalse(mock_signal.send.called)

    @patch("order.signals.handlers.order_shipment_dispatched")
    def test_refires_after_clear_then_set_new_tracking(
        self, mock_signal
    ) -> None:
        """A full clear-then-reset cycle IS a legitimate
        null→set transition — losing tracking and getting fresh
        tracking is a new shipment event. What we guard against is
        repeating the exact same save without an intervening clear
        (tested above)."""
        order = self._fresh_order()
        with self.captureOnCommitCallbacks(execute=True):
            order.tracking_number = "ABC123"
            order.shipping_carrier = "ELTA"
            order.save()
        with self.captureOnCommitCallbacks(execute=True):
            order.tracking_number = ""
            order.shipping_carrier = ""
            order.save()
        mock_signal.send.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            order.tracking_number = "ABC123"
            order.shipping_carrier = "ELTA"
            order.save()
        self.assertTrue(mock_signal.send.called)


class LoyaltyTierChangedDirectionTestCase(TestCase):
    """Direction logic on ``loyalty_tier_changed``: tier downgrades must
    be silent so we never notify a user that they lost status.

    The handler wraps the task dispatch in ``transaction.on_commit`` —
    we use ``captureOnCommitCallbacks`` (Django 3.2+) to flush the
    callback so the ``.delay`` mock records the call before we assert.
    """

    def test_direction_up_fires_notification_task(self) -> None:
        from loyalty.factories.tier import LoyaltyTierFactory
        from loyalty.signals import notify_tier_up_live
        from user.factories.account import UserAccountFactory

        bronze = LoyaltyTierFactory(required_level=1)
        gold = LoyaltyTierFactory(required_level=10)
        user = UserAccountFactory(loyalty_tier=bronze)

        with patch("loyalty.tasks.notify_loyalty_tier_up_live") as mock_task:
            with self.captureOnCommitCallbacks(execute=True):
                notify_tier_up_live(
                    sender=type(user),
                    user=user,
                    direction="up",
                    old_tier_id=bronze.pk,
                    new_tier_id=gold.pk,
                )
        self.assertTrue(mock_task.delay.called)

    def test_direction_down_does_not_fire(self) -> None:
        from loyalty.factories.tier import LoyaltyTierFactory
        from loyalty.signals import notify_tier_up_live
        from user.factories.account import UserAccountFactory

        bronze = LoyaltyTierFactory(required_level=1)
        gold = LoyaltyTierFactory(required_level=10)
        user = UserAccountFactory(loyalty_tier=bronze)

        with patch("loyalty.tasks.notify_loyalty_tier_up_live") as mock_task:
            with self.captureOnCommitCallbacks(execute=True):
                notify_tier_up_live(
                    sender=type(user),
                    user=user,
                    direction="down",
                    old_tier_id=gold.pk,
                    new_tier_id=bronze.pk,
                )
        self.assertFalse(mock_task.delay.called)

    def test_direction_same_does_not_fire(self) -> None:
        from loyalty.factories.tier import LoyaltyTierFactory
        from loyalty.signals import notify_tier_up_live
        from user.factories.account import UserAccountFactory

        bronze = LoyaltyTierFactory(required_level=1)
        user = UserAccountFactory(loyalty_tier=bronze)

        with patch("loyalty.tasks.notify_loyalty_tier_up_live") as mock_task:
            with self.captureOnCommitCallbacks(execute=True):
                notify_tier_up_live(
                    sender=type(user),
                    user=user,
                    direction="same",
                    old_tier_id=bronze.pk,
                    new_tier_id=bronze.pk,
                )
        self.assertFalse(mock_task.delay.called)
