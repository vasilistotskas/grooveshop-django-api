"""Tests for the tenant fanout pattern.

``tenant.celery.run_for_all_tenants(task_name, **kwargs)`` is the
primitive every scheduled cross-tenant job calls (fanout_check_pending_orders,
fanout_sync_meilisearch_indexes, etc.). It iterates active tenants,
excludes the public schema, and dispatches the underlying task with a
``_schema_name`` header so ``TenantTask.__call__`` can switch into the
right tenant schema.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tenant.celery import run_for_all_tenants


class TestRunForAllTenants:
    @pytest.mark.django_db
    def test_dispatches_one_send_per_active_tenant(self, tenant_factory):
        tenant_factory("fanout-active-a")
        tenant_factory("fanout-active-b")

        with patch("core.celery_app.send_task") as send:
            run_for_all_tenants("order.tasks.check_pending_orders")

        # At least these two tenants (backfill 0004 also seeds webside)
        schemas = {
            call.kwargs["headers"]["_schema_name"] for call in send.mock_calls
        }
        assert "fanout_active_a" in schemas
        assert "fanout_active_b" in schemas

    @pytest.mark.django_db
    def test_skips_inactive_tenants(self, tenant_factory):
        active = tenant_factory("fanout-on")
        inactive = tenant_factory("fanout-off")
        inactive.is_active = False
        inactive.save(update_fields=["is_active"])

        with patch("core.celery_app.send_task") as send:
            run_for_all_tenants("order.tasks.check_pending_orders")

        schemas = {
            call.kwargs["headers"]["_schema_name"] for call in send.mock_calls
        }
        assert active.schema_name in schemas
        assert inactive.schema_name not in schemas

    @pytest.mark.django_db
    def test_excludes_public_schema(self, tenant_factory):
        tenant_factory("fanout-tenant-x")

        with patch("core.celery_app.send_task") as send:
            run_for_all_tenants("order.tasks.check_pending_orders")

        schemas = {
            call.kwargs["headers"]["_schema_name"] for call in send.mock_calls
        }
        assert "public" not in schemas

    @pytest.mark.django_db
    def test_passes_task_name_and_kwargs(self, tenant_factory):
        tenant_factory("fanout-kwargs")

        with patch("core.celery_app.send_task") as send:
            run_for_all_tenants("core.tasks.clear_old_history_task", days=90)

        # Every call must target the requested task and carry the kwargs
        # intact (they're what the underlying task accepts as arguments).
        for call in send.mock_calls:
            args, kwargs = call.args, call.kwargs
            assert args[0] == "core.tasks.clear_old_history_task"
            assert kwargs["kwargs"] == {"days": 90}
            assert "_schema_name" in kwargs["headers"]


class TestFanoutTaskWrappers:
    """The ``tenant.tasks.fanout_*`` wrappers delegate to run_for_all_tenants."""

    @pytest.mark.django_db
    def test_fanout_check_pending_orders(self):
        from tenant import tasks as tenant_tasks

        with patch("tenant.tasks.run_for_all_tenants") as run:
            tenant_tasks.fanout_check_pending_orders()

        run.assert_called_once_with("order.tasks.check_pending_orders")

    @pytest.mark.django_db
    def test_fanout_update_order_statuses_from_shipping(self):
        from tenant import tasks as tenant_tasks

        with patch("tenant.tasks.run_for_all_tenants") as run:
            tenant_tasks.fanout_update_order_statuses_from_shipping()

        run.assert_called_once_with(
            "order.tasks.update_order_statuses_from_shipping"
        )

    @pytest.mark.django_db
    def test_fanout_auto_cancel_stuck_pending_orders(self):
        from tenant import tasks as tenant_tasks

        with patch("tenant.tasks.run_for_all_tenants") as run:
            tenant_tasks.fanout_auto_cancel_stuck_pending_orders()

        run.assert_called_once_with(
            "order.tasks.auto_cancel_stuck_pending_orders"
        )

    @pytest.mark.django_db
    def test_fanout_send_checkout_abandonment_emails(self):
        from tenant import tasks as tenant_tasks

        with patch("tenant.tasks.run_for_all_tenants") as run:
            tenant_tasks.fanout_send_checkout_abandonment_emails()

        run.assert_called_once_with(
            "order.tasks.send_checkout_abandonment_emails"
        )

    @pytest.mark.django_db
    def test_fanout_check_low_stock_products(self):
        from tenant import tasks as tenant_tasks

        with patch("tenant.tasks.run_for_all_tenants") as run:
            tenant_tasks.fanout_check_low_stock_products()

        run.assert_called_once_with("product.tasks.check_low_stock_products")
