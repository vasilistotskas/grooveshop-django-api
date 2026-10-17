"""The store dashboard page end to end: markup, and what it costs.

The budget is measured, not guessed: before the dashboard was split
into widgets a warm ``/admin/`` ran 128 queries, 118 of them a context
processor resolving a setting no admin template reads.
"""

from __future__ import annotations

import re

import pytest
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from admin.dashboard.store import queries
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

# Superuser, warm cache: session + user + usersessions, the per-request
# alerts (legal pages, and the settings of the merchant's legal identity)
# and the sidebar's feature switches (eleven store settings and the active
# carriers). Every setting read is a query of its own HERE: the test
# database has no Setting rows, and ``Setting.get`` caches a key only
# once its row is read - in production every declared setting has a row
# and these reads are cache hits. Measured 2026-10-17 (39 warm, 65 cold: the
# follow-up counter reads ORDER_FOLLOW_UP_DAYS); re-measure before raising.
WARM_BUDGET = 39
COLD_BUDGET = 65


@pytest.fixture
def client_():
    client = Client()
    client.force_login(
        UserAccountFactory(admin=True),
        backend="tenant.auth_backends.PlatformStaffBackend",
    )
    return client


def _count(client) -> int:
    with CaptureQueriesContext(connection) as queries_run:
        response = client.get(reverse("admin:index"))
    assert response.status_code == 200
    return len(queries_run.captured_queries)


def _seed(size: int) -> None:
    from contact.factories import ContactFactory
    from order.factories.order import OrderFactory
    from product.enum.review import ReviewStatus
    from product.factories.product import ProductFactory
    from product.factories.review import ProductReviewFactory

    OrderFactory.create_batch(size, num_order_items=0)
    ProductFactory.create_batch(size, active=True, stock=3)
    # Pinned: the factory cycles statuses process-wide, so a one-review
    # seed had no NEW review (and no pending-reviews prefetch) whenever
    # earlier tests on the same worker left the cycle elsewhere.
    ProductReviewFactory.create_batch(size, status=ReviewStatus.NEW)
    ContactFactory.create_batch(size)


def test_the_period_switch_renders_as_links(client_):
    html = client_.get(
        reverse("admin:index"), {"period": "13w", "metric": "orders"}
    ).content.decode()
    for period in queries.PERIODS:
        assert f"period={period.key}" in html
    # One marked link per switch, in page order: the period switch (in
    # the page header) and then the chart's measure switch. Both keep
    # the other's choice in their links.
    marked = re.findall(
        r'href="\?period=(\w+)&amp;metric=(\w+)"\s+aria-current="true"', html
    )
    assert marked == [("13w", "orders"), ("13w", "orders")]


def test_the_footer_names_the_release(client_):
    from django.conf import settings

    assert settings.RELEASE_VERSION
    html = client_.get(reverse("admin:index")).content.decode()
    assert settings.RELEASE_VERSION in html


def test_budget(client_):
    _seed(3)
    cache.clear()
    assert _count(client_) <= COLD_BUDGET
    assert _count(client_) <= WARM_BUDGET


def test_the_cost_does_not_grow_with_the_data(client_):
    # The first request creates the tracked session (an INSERT; later
    # ones UPDATE it), which is not what this measures.
    client_.get(reverse("admin:index"))
    _seed(1)
    cache.clear()
    small = _count(client_)
    _seed(14)
    cache.clear()
    assert _count(client_) == small
