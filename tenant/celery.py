from __future__ import annotations

import logging
from typing import Any

from celery import Task
from django.db import connection

logger = logging.getLogger(__name__)


class TenantTask(Task):
    """Base Celery task that propagates tenant schema context.

    Schema resolution at enqueue time prefers an explicit
    ``_schema_name`` header from the caller (so cross-schema
    on_commit and fanout paths can pin the value) and falls back to
    the thread-local ``connection.schema_name`` (H8 in
    MULTI_TENANT_AUDIT.md). Without the explicit hand-off, dispatchers
    fired from worker callbacks or management commands stamp
    ``'public'`` and the worker runs against the wrong schema.
    """

    def apply_async(self, *args: Any, **options: Any) -> Any:
        headers = options.pop("headers", {}) or {}
        if not headers.get("_schema_name"):
            headers["_schema_name"] = getattr(
                connection, "schema_name", "public"
            )
        options["headers"] = headers
        return super().apply_async(*args, **options)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        schema_name = (
            self.request.get("_schema_name") if self.request else None
        ) or "public"

        from django_tenants.utils import schema_context

        with schema_context(schema_name):
            return super().__call__(*args, **kwargs)


def run_for_all_tenants(task_name: str, **kwargs: Any) -> list:
    """Fan-out a task to all active tenant schemas."""
    from tenant.models import Tenant

    results = []
    # Skip suspended tenants — a suspended operator's beat-driven work
    # (poll carriers, reconcile payouts, sync lockers/stations) must not
    # fire: it would burn the carrier API budget and mutate a frozen
    # tenant's data. Mirrors the webhook resolvers' suspended_at filter.
    for tenant in Tenant.objects.filter(
        is_active=True, suspended_at__isnull=True
    ).exclude(schema_name="public"):
        from core import celery_app

        result = celery_app.send_task(
            task_name,
            kwargs=kwargs,
            headers={"_schema_name": tenant.schema_name},
        )
        results.append(result)
    return results
