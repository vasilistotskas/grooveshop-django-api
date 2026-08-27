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
            call.kwargs["headers"]["_schema_name"]
            for call in send.call_args_list
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
            call.kwargs["headers"]["_schema_name"]
            for call in send.call_args_list
        }
        assert active.schema_name in schemas
        assert inactive.schema_name not in schemas

    @pytest.mark.django_db
    def test_excludes_public_schema(self, tenant_factory):
        tenant_factory("fanout-tenant-x")

        with patch("core.celery_app.send_task") as send:
            run_for_all_tenants("order.tasks.check_pending_orders")

        schemas = {
            call.kwargs["headers"]["_schema_name"]
            for call in send.call_args_list
        }
        assert "public" not in schemas

    @pytest.mark.django_db
    def test_passes_task_name_and_kwargs(self, tenant_factory):
        tenant_factory("fanout-kwargs")

        with patch("core.celery_app.send_task") as send:
            run_for_all_tenants("core.tasks.clear_old_history_task", days=90)

        # Every call must target the requested task and carry the kwargs
        # intact (they're what the underlying task accepts as arguments).
        for call in send.call_args_list:
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

    @pytest.mark.django_db
    def test_fanout_anonymize_old_search_queries(self):
        from tenant import tasks as tenant_tasks

        with patch("tenant.tasks.run_for_all_tenants") as run:
            tenant_tasks.fanout_anonymize_old_search_queries()

        run.assert_called_once_with(
            "search.tasks.anonymize_old_search_queries", days=90
        )

    @pytest.mark.django_db
    def test_fanout_cleanup_expired_data_exports(self):
        from tenant import tasks as tenant_tasks

        with patch("tenant.tasks.run_for_all_tenants") as run:
            tenant_tasks.fanout_cleanup_expired_data_exports()

        run.assert_called_once_with("user.tasks.cleanup_expired_data_exports")

    @pytest.mark.django_db
    def test_fanout_check_stale_acs_shipments(self):
        from tenant import tasks as tenant_tasks

        with patch("tenant.tasks.run_for_all_tenants") as run:
            tenant_tasks.fanout_check_stale_acs_shipments()

        run.assert_called_once_with(
            "shipping_acs.tasks.check_stale_acs_shipments"
        )

    @pytest.mark.django_db
    def test_fanout_clear_expired_sessions(self):
        from tenant import tasks as tenant_tasks

        with patch("tenant.tasks.run_for_all_tenants") as run:
            tenant_tasks.fanout_clear_expired_sessions()

        run.assert_called_once_with("core.tasks.clear_expired_sessions_task")


class TestBeatScheduleTenantCoverage:
    """Every beat entry whose task touches TENANT_APPS-scoped tables must
    dispatch through a ``tenant.tasks.fanout_*`` wrapper — a direct beat
    call fires once in the public schema and silently processes zero rows
    per tenant.

    This is the self-enforcing version of the recurring merge-gap
    checklist: merging a new beat entry from main without a fanout
    wrapper (or without an explicit public-schema allowlist decision
    here) fails this test instead of silently no-opping in production.
    """

    # Tasks that legitimately run ONCE in the public schema — platform
    # infrastructure with no tenant-scoped table access. Adding a task
    # here is an explicit reviewed decision, not a default.
    PUBLIC_SCHEMA_TASKS = {
        "core.tasks.monitor_system_health",
        "core.tasks.scheduled_database_backup",
        "core.tasks.cleanup_old_backups",
        "core.tasks.clear_expired_sessions_task",
        "core.tasks.clear_all_cache_task",
        "core.tasks.clear_development_log_files_task",
        # Billing terms + dunning bookkeeping are public-schema Tenant
        # rows; the cycle never enters a tenant schema (tenant/billing.py).
        "tenant.tasks.process_tenant_billing",
        # TenantArchive rows live in the public schema BECAUSE the
        # schemas they describe have been dropped — there is no tenant
        # left to fan out into.
        "tenant.tasks.purge_expired_tenant_archives",
    }

    def test_every_tenant_scoped_beat_entry_uses_fanout(self):
        from django.conf import settings

        offenders = [
            f"{name} -> {entry['task']}"
            for name, entry in settings.CELERY_BEAT_SCHEDULE.items()
            if entry["task"] not in self.PUBLIC_SCHEMA_TASKS
            and not entry["task"].startswith("tenant.tasks.fanout_")
        ]
        assert not offenders, (
            "Beat entries dispatch tenant-scoped tasks directly — add a "
            "fanout_* wrapper in tenant/tasks.py (or allowlist a genuinely "
            f"public-schema task above): {offenders}"
        )

    def test_fanout_beat_entries_carry_no_kwargs(self):
        from django.conf import settings

        offenders = [
            name
            for name, entry in settings.CELERY_BEAT_SCHEDULE.items()
            if entry["task"].startswith("tenant.tasks.fanout_")
            and "kwargs" in entry
        ]
        assert not offenders, (
            "fanout_* wrappers take no arguments — kwargs belong in the "
            "wrapper body, or beat raises TypeError every tick: "
            f"{offenders}"
        )

    def test_every_fanout_beat_target_exists(self):
        from django.conf import settings
        from tenant import tasks as tenant_tasks

        missing = [
            entry["task"]
            for entry in settings.CELERY_BEAT_SCHEDULE.values()
            if entry["task"].startswith("tenant.tasks.fanout_")
            and not hasattr(tenant_tasks, entry["task"].rsplit(".", 1)[1])
        ]
        assert not missing, f"Beat points at missing fanout tasks: {missing}"


class TestFanoutReturnIsSerializable:
    """The fan-out return value must survive Celery's result encoder.

    Celery encodes a task's return value into the result backend with
    the configured serializer (JSON). Returning ``AsyncResult`` objects
    raised ``EncodeError`` *after* every subtask had already been
    dispatched: the scheduled work ran, but the fanout task itself was
    recorded FAILED on every beat tick, so genuine failures were
    indistinguishable from the constant noise. Observed in production
    2026-08-21 across all six beat-driven fanouts.
    """

    @pytest.mark.django_db
    def test_return_value_is_json_serializable(self, tenant_factory):
        import json

        from kombu.serialization import dumps

        tenant_factory("fanout-json-a")

        with patch("core.celery_app.send_task") as send:
            send.return_value.id = "00000000-0000-0000-0000-000000000001"
            result = run_for_all_tenants("order.tasks.check_pending_orders")

        # Plain json first — the readable assertion.
        json.dumps(result)
        # Then the encoder Celery actually uses for the result backend.
        dumps(result, serializer="json")

        assert result, "expected at least one dispatch record"
        for record in result:
            assert set(record) == {"schema_name", "task_id"}
            assert isinstance(record["schema_name"], str)
            assert isinstance(record["task_id"], str)
