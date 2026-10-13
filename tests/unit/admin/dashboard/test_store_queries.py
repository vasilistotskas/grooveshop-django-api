"""The store dashboard's cached queries: what each one counts.

Each query is called through ``.compute`` - the uncached body - so a
test sees exactly the database it built. Period queries take the
period's key.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from django.conf import settings
from django.utils import timezone
from djmoney.money import Money

from admin.dashboard.store import queries
from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models.order import Order

pytestmark = pytest.mark.django_db


#: What an unpaid order owes. Nonzero on purpose: an unpaid order owes its
#: full amount, so a query that counted it would show.
UNPAID_AMOUNT_DUE = 999


def _order(*, paid: float | None = None, days_ago: int = 0, **fields):
    """A COMPLETED order that owed ``paid``, or a PENDING one when ``paid``
    is ``None``."""
    order = OrderFactory(
        num_order_items=0,
        payment_status=(
            PaymentStatus.COMPLETED
            if paid is not None
            else PaymentStatus.PENDING
        ),
        amount_due=Money(
            UNPAID_AMOUNT_DUE if paid is None else paid,
            settings.DEFAULT_CURRENCY,
        ),
        **fields,
    )
    # created_at is auto_now_add; backdate through the queryset.
    Order.objects.filter(pk=order.pk).update(
        created_at=timezone.now() - timedelta(days=days_ago)
    )
    return order


class TestPeriods:
    def test_each_period_is_whole_buckets_ending_with_the_current_one(self):
        today = timezone.localdate()

        days = queries.PERIODS_BY_KEY["7d"].buckets()
        assert len(days) == 7
        assert days[-1] == today

        weeks = queries.PERIODS_BY_KEY["13w"].buckets()
        assert len(weeks) == 13
        assert all(week.weekday() == 0 for week in weeks)
        assert weeks[-1] <= today < weeks[-1] + timedelta(weeks=1)

        months = queries.PERIODS_BY_KEY["12m"].buckets()
        assert len(months) == 12
        assert all(month.day == 1 for month in months)
        assert months[-1] == date(today.year, today.month, 1)

    def test_a_period_starts_at_local_midnight_of_its_first_bucket(self):
        period = queries.PERIODS_BY_KEY["30d"]
        prior_start, start, now = period.window()

        assert start == timezone.make_aware(
            datetime.combine(period.buckets()[0], time.min)
        )
        assert start - prior_start == now - start

    def test_the_period_before_is_as_long_across_a_dst_change(
        self, monkeypatch
    ):
        """Athens leaves summer time on 25 Oct 2026: a 7-day period from
        26 Oct is compared with a span that crosses the change. Wall-clock
        arithmetic made that span an hour longer."""
        from datetime import UTC

        from django.utils import timezone as django_timezone

        fixed = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
        monkeypatch.setattr(django_timezone, "now", lambda: fixed)
        with django_timezone.override("Europe/Athens"):
            prior_start, start, now = queries.PERIODS_BY_KEY["7d"].window()

        assert start - prior_start == now - start

    def test_an_unknown_period_is_refused(self):
        with pytest.raises(ValueError):
            queries.overview.get("forever")


class TestOverview:
    def test_revenue_against_the_period_before(self):
        _order(paid=100, days_ago=2)
        _order(paid=50, days_ago=10)

        data = queries.overview.compute("7d")

        assert data["revenue"] == 100.0
        assert data["prior_revenue"] == 50.0

    def test_unpaid_orders_are_orders_but_not_revenue(self):
        _order(paid=None)
        _order(paid=40)

        data = queries.overview.compute("30d")

        assert data["revenue"] == 40.0
        assert data["orders"] == 2
        assert data["paid"] == 1

    def test_repeat_customers_count_people_with_two_paid_orders(self):
        from user.factories.account import UserAccountFactory

        loyal, once = UserAccountFactory.create_batch(2)
        _order(paid=10, user=loyal)
        _order(paid=10, user=loyal)
        _order(paid=10, user=once)
        _order(paid=10, user=None)  # a guest has no identity to repeat

        data = queries.overview.compute("30d")

        assert (data["paying_customers"], data["repeat_customers"]) == (2, 1)

    def test_new_customers_are_accounts_created_in_the_period(self):
        from user.factories.account import UserAccountFactory
        from user.models.account import UserAccount

        before = queries.overview.compute("7d")["new_customers"]
        old = UserAccountFactory()
        UserAccount.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=9)
        )
        UserAccountFactory()

        data = queries.overview.compute("7d")
        assert data["new_customers"] == before + 1
        assert data["prior_new_customers"] >= 1

    def test_three_queries(self, django_assert_num_queries):
        _order(paid=10)
        with django_assert_num_queries(3):
            queries.overview.compute("30d")


class TestSalesSeries:
    def test_one_bucket_per_local_day(self):
        _order(paid=40, days_ago=1)
        _order(paid=None, days_ago=1)

        days = queries.sales_series.compute("30d")

        assert len(days) == 30
        assert days[-1]["start"] == timezone.localdate()
        yesterday = days[-2]
        assert (yesterday["orders"], yesterday["paid"]) == (2, 1)
        assert yesterday["revenue"] == 40.0

    def test_the_bars_add_up_to_the_headline(self):
        _order(paid=40, days_ago=1)
        _order(paid=25, days_ago=40)
        _order(paid=10, days_ago=200)

        for key in queries.PERIOD_KEYS:
            series = queries.sales_series.compute(key)
            overview = queries.overview.compute(key)
            assert sum(row["revenue"] for row in series) == overview["revenue"]
            assert sum(row["orders"] for row in series) == overview["orders"]

    def test_months_are_calendar_months(self):
        months = queries.sales_series.compute("12m")

        assert [row["start"] for row in months] == (
            queries.PERIODS_BY_KEY["12m"].buckets()
        )


class TestBestSellers:
    def _sold(self, product, quantity, *, paid=True, refunded=0):
        from order.factories.item import OrderItemFactory

        order = _order(paid=10 if paid else None)
        OrderItemFactory(
            order=order,
            product=product,
            quantity=quantity,
            original_quantity=quantity,
            refunded_quantity=refunded,
            is_refunded=bool(refunded),
        )

    def test_units_paid_for_net_of_refunds(self):
        from product.factories.product import ProductFactory

        mug, bag, candle = ProductFactory.create_batch(3)
        self._sold(mug, 2)
        self._sold(mug, 3, refunded=1)
        self._sold(bag, 5)
        self._sold(candle, 9, paid=False)

        rows = queries.best_sellers.compute("30d")

        assert [(row["id"], row["units"]) for row in rows] == [
            (bag.id, 5),
            (mug.id, 4),
        ]

    def test_at_most_the_top_few(self):
        from product.factories.product import ProductFactory

        for product in ProductFactory.create_batch(queries.BEST_SELLERS + 2):
            self._sold(product, 1)

        assert len(queries.best_sellers.compute("30d")) == queries.BEST_SELLERS


class TestOrderSources:
    def _attributed(self, source: str, source_type: str, *, days_ago: int = 0):
        from order.factories import OrderAttributionFactory

        attribution = OrderAttributionFactory(
            source=source, source_type=source_type
        )
        Order.objects.filter(pk=attribution.order_id).update(
            created_at=timezone.now() - timedelta(days=days_ago)
        )

    def test_counts_per_source_in_the_period(self):
        from order.enum.attribution import OrderSourceType

        self._attributed("instagram", OrderSourceType.SOCIAL)
        self._attributed("instagram", OrderSourceType.CAMPAIGN)
        self._attributed("google", OrderSourceType.SEARCH)
        self._attributed("google", OrderSourceType.SEARCH, days_ago=45)
        OrderFactory(num_order_items=0)  # placed before attribution

        data = queries.order_sources.compute("30d")

        assert [(row["source"], row["count"]) for row in data["top"]] == [
            ("instagram", 2),
            ("google", 1),
        ]
        assert (data["other"], data["total"]) == (0, 3)

    def test_sources_past_the_top_fold_into_other(self):
        from order.enum.attribution import OrderSourceType

        for index in range(queries.ORDER_SOURCE_TOP_N + 2):
            self._attributed(
                f"site{index}.example.org", OrderSourceType.REFERRAL
            )

        data = queries.order_sources.compute("30d")

        assert len(data["top"]) == queries.ORDER_SOURCE_TOP_N
        assert data["other"] == 2

    def test_a_tie_is_ordered_by_source_name(self):
        from order.enum.attribution import OrderSourceType

        self._attributed("zeta.example.org", OrderSourceType.REFERRAL)
        self._attributed("alpha.example.org", OrderSourceType.REFERRAL)

        assert [
            row["source"] for row in queries.order_sources.compute("30d")["top"]
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
            queries.order_sources.compute("30d")


class TestFunnel:
    def test_counts_orders_and_open_carts_as_started(self):
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

        assert queries.funnel.compute("30d") == {
            "started": 4,
            "orders": 2,
            "paid": 1,
        }


class TestSearch:
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

        data = queries.search.compute("30d")

        assert data["searches"] == 3
        top = [row["query"] for row in data["top_queries"]]
        assert "Po" not in top
        assert "bataria kinitou" in top
        assert [row["query"] for row in data["zero_queries"]] == ["optiki ina"]

    def test_a_query_another_lane_answered_found_something(self):
        self._query("windows", results_count=0)
        self._query("windows", results_count=11)
        self._query("asirmati skoupa", results_count=0)
        self._query("asirmati skoupa", results_count=0)

        data = queries.search.compute("30d")

        assert [row["query"] for row in data["zero_queries"]] == [
            "asirmati skoupa"
        ]
        assert data["zero"] == 2
        assert data["meaningful"] == 4

    def test_old_rows_are_outside_the_period(self):
        self._query("klip kalodion", days_ago=45)

        assert queries.search.compute("30d")["searches"] == 0
        assert queries.search.compute("12m")["searches"] == 1

    def test_clicks_are_none_until_any_click_is_recorded(self):
        from search.models import SearchClick

        row = self._query("power bank")
        assert queries.search.compute("30d")["clicks"] is None

        SearchClick.objects.create(
            search_query=row, result_id="2", result_type="product", position=0
        )
        assert queries.search.compute("30d")["clicks"] == 1


class TestAttentionCounts:
    def test_pending_orders(self):
        _order(status=OrderStatus.PENDING)
        _order(status=OrderStatus.COMPLETED, paid=10)

        assert queries.attention_counts.compute()["pending_orders"] == 1

    def test_new_messages_are_the_last_seven_days(self):
        from contact.factories import ContactFactory
        from contact.models import Contact

        ContactFactory()
        old = ContactFactory()
        Contact.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=8)
        )

        assert queries.attention_counts.compute()["new_contact_messages"] == 1

    def test_low_stock_is_the_warning_band_only(self):
        from product.factories.product import ProductFactory

        ProductFactory(stock=0, active=True)
        ProductFactory(stock=5, active=True)
        ProductFactory(stock=15, active=True)

        assert queries.attention_counts.compute()["low_stock"] == 1
