"""The store dashboard page end to end: markup, and what it costs.

The budget is measured, not guessed: before the dashboard was split
into widgets a warm ``/admin/`` ran 128 queries, 118 of them a context
processor resolving a setting no admin template reads.
"""

from __future__ import annotations

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
# alerts (settings, legal pages) and low stock. Re-measure before
# raising.
WARM_BUDGET = 22
COLD_BUDGET = 55


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
    from product.factories.product import ProductFactory
    from product.factories.review import ProductReviewFactory

    OrderFactory.create_batch(size, num_order_items=0)
    ProductFactory.create_batch(size, active=True, stock=3)
    ProductReviewFactory.create_batch(size)
    ContactFactory.create_batch(size)


def test_the_revenue_window_switch_renders(client_):
    html = client_.get(reverse("admin:index")).content.decode()
    for key, _days in queries.REVENUE_PERIODS:
        assert f'data-period="{key}"' in html
    assert "dashboard-revenue-period" in html


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
