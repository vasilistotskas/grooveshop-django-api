"""``Order.is_first_order`` — the Google Ads ``new_customer`` signal.

Google asks for the parameter to be calculated, not hardcoded; a wrong
value skews new-customer acquisition reporting for every campaign that
optimises on it. Identity is the account when there is one and the
email otherwise, so a guest who later registers stays one customer.
"""

from __future__ import annotations

from datetime import timedelta
from typing import cast

import pytest
from django.utils import timezone

from order.enum.status import OrderStatus
from order.factories.order import OrderFactory
from order.models import Order
from user.factories.account import UserAccountFactory


def _backdate(order: Order, days: int) -> None:
    Order.objects.filter(pk=order.pk).update(
        created_at=timezone.now() - timedelta(days=days)
    )
    order.refresh_from_db()


def _earlier(**kwargs) -> Order:
    """A prior order that COUNTS.

    The factory picks a random status, and a canceled one is excluded
    by design — which made this suite pass or fail on the draw (CI
    shard 1, 2026-09-17). Pinned to a status that counts.
    """
    order = cast(Order, OrderFactory(status=OrderStatus.PENDING, **kwargs))
    _backdate(order, days=3)
    return order


@pytest.mark.django_db
class TestIsFirstOrder:
    def test_the_only_order_is_the_first(self) -> None:
        order = OrderFactory()

        assert order.is_first_order is True

    def test_an_earlier_order_by_the_same_account_makes_it_a_repeat(
        self,
    ) -> None:
        user = UserAccountFactory()
        earlier = _earlier(user=user)
        later = OrderFactory(user=user)

        assert later.is_first_order is False
        assert earlier.is_first_order is True

    def test_a_canceled_earlier_order_does_not_count(self) -> None:
        # A checkout that never completed is not a prior purchase.
        user = UserAccountFactory()
        canceled = OrderFactory(user=user, status=OrderStatus.CANCELED)
        _backdate(canceled, days=3)
        later = OrderFactory(user=user)

        assert later.is_first_order is True

    def test_guests_are_matched_by_email_case_insensitively(self) -> None:
        _earlier(user=None, email="Repeat@Example.com")
        later = OrderFactory(user=None, email="repeat@example.com")

        assert later.is_first_order is False

    def test_a_different_guest_email_is_a_first_order(self) -> None:
        _earlier(user=None, email="one@example.com")
        later = OrderFactory(user=None, email="two@example.com")

        assert later.is_first_order is True

    def test_another_accounts_order_does_not_count(self) -> None:
        _earlier(user=UserAccountFactory())
        later = OrderFactory(user=UserAccountFactory())

        assert later.is_first_order is True
