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
    the thread-local ``connection.schema_name``. Without the
    explicit hand-off, dispatchers
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

        from django_tenants.utils import (
            get_public_schema_name,
            schema_context,
            tenant_context,
        )

        if schema_name == get_public_schema_name():
            with schema_context(schema_name):
                return super().__call__(*args, **kwargs)

        # ``schema_context(schema_name)`` only sets ``connection.tenant``
        # to a bare ``FakeTenant(schema_name=...)`` (django-tenants
        # internal — carries no other field). Every ``tenant.credentials.*``
        # helper reads real fields off ``connection.tenant``
        # (``getattr(tenant, "acs_api_key", "")`` etc.), so a task running
        # under a FakeTenant would see every per-tenant credential as
        # unconfigured regardless of what the tenant actually has set.
        # ``tenant_context(tenant)`` sets ``connection.tenant`` to the
        # real row instead.
        from tenant.models import Tenant

        try:
            tenant = Tenant.objects.get(schema_name=schema_name)
        except Tenant.DoesNotExist:
            logger.warning(
                "TenantTask: no Tenant row for schema=%s — falling back to "
                "a bare schema context (tenant.credentials.* will read as "
                "unconfigured)",
                schema_name,
            )
            with schema_context(schema_name):
                return super().__call__(*args, **kwargs)

        with tenant_context(tenant):
            return super().__call__(*args, **kwargs)


def run_for_all_tenants(task_name: str, **kwargs: Any) -> list[dict[str, str]]:
    """Fan-out a task to all active tenant schemas.

    Returns JSON-serializable dispatch records, NOT ``AsyncResult``
    objects. Celery encodes a task's return value into the result
    backend using the configured serializer (JSON here), and an
    ``AsyncResult`` is not JSON-serializable — so returning them raised
    ``EncodeError`` *after* the fan-out had already dispatched every
    subtask. The scheduled work ran fine, but each fanout task was
    recorded as FAILED, so every beat tick produced a failure and real
    failures were indistinguishable from the noise. Observed in
    production 2026-08-21 across all six beat-driven fanouts.
    """
    from tenant.models import Tenant

    results: list[dict[str, str]] = []
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
        results.append(
            {"schema_name": tenant.schema_name, "task_id": str(result.id)}
        )
    return results
