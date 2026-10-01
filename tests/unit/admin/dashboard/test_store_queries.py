"""The store dashboard's cached queries: what each one counts.

Each query is called through ``.compute`` - the uncached body - so a
test sees exactly the database it built.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.conf import settings
from django.utils import timezone
from djmoney.money import Money

from admin.dashboard.store import queries
from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models.order import Order

pytestmark = pytest.mark.django_db


def _order(*, paid: float | None = None, days_ago: int = 0, **fields):
    order = OrderFactory(
        num_order_items=0,
        payment_status=(
            PaymentStatus.COMPLETED
            if paid is not None
            else PaymentStatus.PENDING
        ),
        paid_amount=Money(paid or 0, settings.DEFAULT_CURRENCY),
        **fields,
    )
    # created_at is auto_now_add; backdate through the queryset.
    Order.objects.filter(pk=order.pk).update(
        created_at=timezone.now() - timedelta(days=days_ago)
    )
    return order


class TestKpis:
    def test_revenue_per_window_with_trend_against_the_window_before(self):
        _order(paid=100, days_ago=2)
        _order(paid=50, days_ago=10)

        revenue = queries.kpis.compute()["revenue"]

        assert list(revenue) == [key for key, _days in queries.REVENUE_PERIODS]
        assert revenue["7d"] == {"amount": 100.0, "trend_pct": 100.0}
        assert revenue["30d"] == {"amount": 150.0, "trend_pct": None}
        assert revenue["365d"]["amount"] == 150.0

    def test_unpaid_orders_are_not_revenue(self):
        OrderFactory(
            num_order_items=0,
            payment_status=PaymentStatus.PENDING,
            paid_amount=Money(999, settings.DEFAULT_CURRENCY),
        )
        assert queries.kpis.compute()["revenue"]["7d"]["amount"] == 0.0

    def test_average_order_value_is_over_paid_orders(self):
        """An unpaid order's ``paid_amount`` is 0 until it is paid;
        averaging it in understated the figure."""
        _order(paid=100)
        _order(paid=50)
        _order(paid=None)

        assert queries.kpis.compute()["avg_order_value"] == 75.0

    def test_pending_orders(self):
        _order(status=OrderStatus.PENDING)
        _order(status=OrderStatus.COMPLETED, paid=10)

        assert queries.kpis.compute()["pending_orders"] == 1

    def test_two_queries(self, django_assert_num_queries):
        _order(paid=10)
        with django_assert_num_queries(2):
            queries.kpis.compute()


class TestPerformance:
    def test_one_bucket_per_local_day(self):
        _order(paid=40, days_ago=1)
        _order(paid=None, days_ago=1)

        days = queries.performance.compute()

        assert len(days) == queries.CHART_DAYS
        assert days[-1]["day"] == timezone.localdate()
        yesterday = days[-2]
        assert yesterday["orders"] == 2
        assert yesterday["revenue"] == 40.0


class TestOrderSource:
    def _attributed(self, source: str, source_type: str, *, days_ago: int = 0):
        from order.factories import OrderAttributionFactory

        attribution = OrderAttributionFactory(
            source=source, source_type=source_type
        )
        Order.objects.filter(pk=attribution.order_id).update(
            created_at=timezone.now() - timedelta(days=days_ago)
        )

    def test_counts_per_source_in_the_last_30_days(self):
        from order.enum.attribution import OrderSourceType

        self._attributed("instagram", OrderSourceType.SOCIAL)
        self._attributed("instagram", OrderSourceType.CAMPAIGN)
        self._attributed("google", OrderSourceType.SEARCH)
        self._attributed("google", OrderSourceType.SEARCH, days_ago=45)
        OrderFactory(num_order_items=0)  # placed before attribution

        data = queries.order_source.compute()

        assert [(row["source"], row["count"]) for row in data["top"]] == [
            ("instagram", 2),
            ("google", 1),
        ]
        assert data["other"] == 0

    def test_sources_past_the_top_fold_into_other(self):
        from order.enum.attribution import OrderSourceType

        for index in range(queries.ORDER_SOURCE_TOP_N + 2):
            self._attributed(
                f"site{index}.example.org", OrderSourceType.REFERRAL
            )

        data = queries.order_source.compute()

        assert len(data["top"]) == queries.ORDER_SOURCE_TOP_N
        assert data["other"] == 2

    def test_a_tie_is_ordered_by_source_name(self):
        from order.enum.attribution import OrderSourceType

        self._attributed("zeta.example.org", OrderSourceType.REFERRAL)
        self._attributed("alpha.example.org", OrderSourceType.REFERRAL)

        assert [
            row["source"] for row in queries.order_source.compute()["top"]
        ] == [
            "alpha.example.org",
            "zeta.example.org",
        ]

    def test_two_queries_however_many_sources(self, django_assert_num_queries):
        from order.enum.attribution import OrderSourceType

        for index in range(queries.ORDER_SOURCE_TOP_N * 3):
            self._attributed(
                f"site{index}.example.org", OrderSourceType.REFERRAL
            )

        with django_assert_num_queries(2):
            queries.order_source.compute()


class TestGrowth:
    def test_funnel_counts_orders_and_open_carts_as_started(self):
        """Placing an order deletes its cart, so the open carts are the
        ones that did not convert; an empty cart is not a start."""
        from cart.factories.cart import CartFactory
        from user.factories.account import UserAccountFactory

        _order(paid=10)
        _order(paid=None)
        users = UserAccountFactory.create_batch(3)
        for user in users[:2]:
            CartFactory(user=user, num_cart_items=1)
        CartFactory(user=users[2])

        assert queries.funnel.compute() == {
            "started": 4,
            "orders": 2,
            "paid": 1,
        }

    def test_repeat_customers_count_people_with_two_paid_orders(self):
        from user.factories.account import UserAccountFactory

        loyal, once = UserAccountFactory.create_batch(2)
        _order(paid=10, user=loyal)
        _order(paid=10, user=loyal)
        _order(paid=10, user=once)
        _order(paid=10, user=None)  # a guest has no identity to repeat

        assert queries.retention.compute() == {"paying": 2, "repeat": 1}


class TestSearchInsights:
    """Validated against production shapes: mostly empty placeholder
    rows, per-keystroke fragments ("P", "Po") and greeklish queries."""

    def _query(self, query, results_count=5, days_ago=0):
        from search.models import SearchQuery

        row = SearchQuery.objects.create(
            query=query,
            language_code="el",
            content_type="federated",
            results_count=results_count,
            estimated_total_hits=results_count,
        )
        if days_ago:
            SearchQuery.objects.filter(pk=row.pk).update(
                timestamp=timezone.now() - timedelta(days=days_ago)
            )
        return row

    def test_empty_and_fragment_queries_are_excluded(self):
        self._query("")
        self._query("Po", results_count=0)
        self._query("bataria kinitou")
        self._query("optiki ina", results_count=0)

        data = queries.search_insights.compute()

        assert data["searches"] == 3
        top = [row["query"] for row in data["top_queries"]]
        assert "Po" not in top
        assert "bataria kinitou" in top
        assert [row["query"] for row in data["zero_queries"]] == ["optiki ina"]

    def test_a_query_another_lane_answered_is_not_zero_result(self):
        self._query("windows", results_count=0)
        self._query("windows", results_count=11)
        self._query("asirmati skoupa", results_count=0)
        self._query("asirmati skoupa", results_count=0)

        data = queries.search_insights.compute()

        assert [row["query"] for row in data["zero_queries"]] == [
            "asirmati skoupa"
        ]
        assert data["zero"] == 2
        assert data["meaningful"] == 4

    def test_old_rows_are_outside_the_window(self):
        self._query("klip kalodion", days_ago=45)
        assert queries.search_insights.compute()["searches"] == 0

    def test_clicks_are_none_until_any_click_is_recorded(self):
        from search.models import SearchClick

        row = self._query("power bank")
        assert queries.search_insights.compute()["clicks"] is None

        SearchClick.objects.create(
            search_query=row, result_id="2", result_type="product", position=0
        )
        assert queries.search_insights.compute()["clicks"] == 1


class TestSidebarCounts:
    def test_new_messages_are_the_last_seven_days(self):
        from contact.factories import ContactFactory
        from contact.models import Contact

        ContactFactory()
        old = ContactFactory()
        Contact.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=8)
        )

        assert queries.sidebar_counts.compute()["new_contact_messages"] == 1

    def test_low_stock_is_the_warning_band_only(self):
        from product.factories.product import ProductFactory

        ProductFactory(stock=0, active=True)
        ProductFactory(stock=5, active=True)
        ProductFactory(stock=15, active=True)

        assert queries.sidebar_counts.compute()["low_stock"] == 1
