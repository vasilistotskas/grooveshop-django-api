"""The order admin's follow-up flag: badge, filter, note and the counter.

Every surface reads ``OrderQuerySet.needing_follow_up``, so they agree by
construction; these tests pin that the admin really routes through it and
that the badge costs no query per row.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from extra_settings.models import Setting

from admin.dashboard.store import queries
from order.enum.status import OrderStatus
from order.factories import OrderFactory
from order.follow_up import FOLLOW_UP_DAYS_SETTING
from order.models.order import Order
from shipping_acs.factories import AcsShipmentFactory
from tests.utils.orders import courier_cash_order

User = get_user_model()

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]


@pytest.fixture(autouse=True)
def _fresh_cache():
    cache.clear()


@pytest.fixture
def client():
    user = User.objects.create_superuser(
        username="boss", email="boss@example.com", password="x"
    )
    client = Client()
    client.force_login(
        user, backend="tenant.auth_backends.PlatformStaffBackend"
    )
    return client


def _order(*, days_ago: int, **fields):
    """A cash-on-delivery order placed ``days_ago`` days back."""
    fields.setdefault("status", OrderStatus.PROCESSING)
    order = courier_cash_order(**fields)
    Order.objects.filter(pk=order.pk).update(
        created_at=timezone.now() - timedelta(days=days_ago)
    )
    return order


def _changelist(client, query: str = ""):
    response = client.get(f"{reverse('admin:order_order_changelist')}?{query}")
    assert response.status_code == 200
    return response


def _listed(client, query: str) -> set[int]:
    return {
        order.pk
        for order in _changelist(client, query).context["cl"].result_list
    }


class TestFilter:
    def test_yes_lists_the_flagged_orders_and_no_the_rest(self, client):
        waiting = _order(days_ago=30)
        recent = _order(days_ago=1)
        done = _order(days_ago=30, status=OrderStatus.COMPLETED)

        assert _listed(client, "follow_up=yes") == {waiting.pk}
        assert _listed(client, "follow_up=no") == {recent.pk, done.pk}

    def test_off_flags_nothing(self, client):
        Setting.objects.update_or_create(
            name=FOLLOW_UP_DAYS_SETTING,
            defaults={"value_type": "int", "value_int": 0},
        )
        order = _order(days_ago=90)

        assert _listed(client, "follow_up=yes") == set()
        assert _listed(client, "follow_up=no") == {order.pk}


class TestBadge:
    def test_a_flagged_row_carries_the_blue_badge_beside_the_status(
        self, client
    ):
        _order(days_ago=30)

        html = _changelist(client).content.decode()

        # The status pills are blue too: match the badge by its text.
        assert re.search(r"bg-blue-100[^>]*>\s*Follow up\s*<", html)

    def test_a_quiet_list_has_no_badge(self, client):
        _order(days_ago=1)

        html = _changelist(client).content.decode()

        assert not re.search(r">\s*Follow up\s*<", html)

    def test_the_badge_adds_no_query_per_row(self, client):
        _order(days_ago=30)
        OrderFactory(num_order_items=0)
        _changelist(client)  # warm per-request caches
        with CaptureQueriesContext(connection) as few:
            _changelist(client)

        for _ in range(4):
            _order(days_ago=30)
        _changelist(client)
        with CaptureQueriesContext(connection) as many:
            _changelist(client)

        assert len(many) == len(few)


class TestChangePage:
    def _page(self, client, order) -> str:
        response = client.get(
            reverse("admin:order_order_change", args=[order.pk])
        )
        assert response.status_code == 200
        return response.content.decode()

    def test_says_why_and_since_when_with_the_last_carrier_event(self, client):
        order = _order(days_ago=30, status=OrderStatus.SHIPPED)
        AcsShipmentFactory(
            order=order, last_event_at=timezone.now() - timedelta(days=9)
        )

        html = self._page(client, order)

        assert "Needs follow-up since" in html
        assert "In transit" in html
        assert "Last carrier event" in html
        assert "none yet" not in html

    def test_an_unshipped_order_has_no_carrier_event_yet(self, client):
        order = _order(days_ago=30, status=OrderStatus.PENDING)

        html = self._page(client, order)

        assert "Not shipped yet" in html
        assert "none yet" in html

    def test_a_quiet_order_has_no_follow_up_line(self, client):
        order = _order(days_ago=1)

        assert "Needs follow-up since" not in self._page(client, order)


class TestCounter:
    def test_counts_the_same_orders_the_filter_lists(self):
        _order(days_ago=30)
        _order(days_ago=30, status=OrderStatus.SHIPPED)
        _order(days_ago=1)
        _order(days_ago=30, status=OrderStatus.DELIVERED)

        assert queries.attention_counts.compute()["follow_up_orders"] == 2

    def test_changing_the_threshold_refreshes_the_cached_count(self):
        _order(days_ago=30)
        assert queries.attention_counts.get()["follow_up_orders"] == 1

        Setting.objects.update_or_create(
            name=FOLLOW_UP_DAYS_SETTING,
            defaults={"value_type": "int", "value_int": 0},
        )

        assert queries.attention_counts.get()["follow_up_orders"] == 0

    def test_the_dashboard_row_opens_the_filtered_list(self, client):
        from django.test import RequestFactory

        from admin.dashboard.store import widgets
        from tests.utils.staff import stamp_platform_identity
        from user.factories.account import UserAccountFactory

        _order(days_ago=30)
        request = RequestFactory().get("/admin/")
        request.user = stamp_platform_identity(UserAccountFactory(admin=True))

        rows = {
            str(row["label"]): row
            for row in widgets.StoreAttention(
                request=request
            ).get_context_data()["rows"]
        }

        assert rows["Orders to follow up"]["count"] == "1"
        assert rows["Orders to follow up"]["href"].endswith("follow_up=yes")
