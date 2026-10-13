"""Dashboard query caching: keys, scope and invalidation."""

from __future__ import annotations

import pytest
from django.conf import settings
from django.core.cache import cache
from unfold.components import ComponentRegistry

from admin.dashboard import cache as dashboard_cache
from admin.dashboard.store import queries

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _fresh_cache():
    cache.clear()


def test_every_key_is_under_the_purgeable_prefix():
    """The "settings" cache surface clears ``*admin:dashboard*``."""
    for query in dashboard_cache._QUERIES:
        for key in query.all_keys():
            assert key.startswith("admin:dashboard:"), key


def test_a_key_per_period_and_per_language_where_they_vary():
    assert len(queries.recent_orders.all_keys()) == 1
    assert len(queries.overview.all_keys()) == len(queries.PERIODS)
    assert len(queries.best_sellers.all_keys()) == len(queries.PERIODS) * len(
        settings.LANGUAGES
    )


def test_a_period_query_caches_each_period_apart():
    queries.overview.get("7d")

    assert cache.get(queries.overview.key("7d")) is not None
    assert cache.get(queries.overview.key("30d")) is None


def test_an_order_write_clears_order_queries_after_commit(
    django_capture_on_commit_callbacks,
):
    from order.factories.order import OrderFactory

    queries.overview.get("7d")
    queries.overview.get("30d")
    queries.search.get("30d")

    with django_capture_on_commit_callbacks(execute=True):
        OrderFactory(num_order_items=0)

    assert cache.get(queries.overview.key("7d")) is None
    assert cache.get(queries.overview.key("30d")) is None
    assert cache.get(queries.search.key("30d")) is not None


def test_the_clear_waits_for_the_commit(monkeypatch):
    """A read between the write and its commit must not re-cache the
    old answer, so the delete is an ``on_commit`` callback. (The suite
    runs ``on_commit`` callbacks immediately, hence the capture.)"""
    from order.factories.order import OrderFactory

    pending = []
    monkeypatch.setattr(
        dashboard_cache.transaction, "on_commit", pending.append
    )
    queries.recent_orders.get()
    OrderFactory(num_order_items=0)

    assert cache.get(queries.recent_orders.key()) is not None
    for callback in pending:
        callback()
    assert cache.get(queries.recent_orders.key()) is None


def test_a_login_clears_nothing(django_capture_on_commit_callbacks):
    """Every login saves the user row (``last_login``); customer counts
    are bounded by the TTL instead."""
    from user.factories.account import UserAccountFactory

    user = UserAccountFactory()
    queries.overview.get("30d")
    with django_capture_on_commit_callbacks(execute=True):
        user.save(update_fields=["last_login"])

    assert cache.get(queries.overview.key("30d")) is not None


def test_widget_classes_are_prefixed_by_site():
    """Unfold's component registry is global and keyed by class name."""
    names = [
        name
        for name, cls in ComponentRegistry._registry.items()
        if cls.__module__.startswith("admin.dashboard")
    ]
    assert names
    for name in names:
        assert name.startswith(("Store", "Platform")), name
