from __future__ import annotations

import logging
from typing import Any

from celery import Task
from django.db import connection

logger = logging.getLogger(__name__)


class TenantTask(Task):
    """Base Celery task that propagates tenant schema context."""

    def apply_async(self, args=None, kwargs=None, **options):
        headers = options.pop("headers", {}) or {}
        headers["_schema_name"] = getattr(connection, "schema_name", "public")
        options["headers"] = headers
        return super().apply_async(args=args, kwargs=kwargs, **options)

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
    for tenant in Tenant.objects.filter(is_active=True).exclude(
        schema_name="public"
    ):
        from core import celery_app

        result = celery_app.send_task(
            task_name,
            kwargs=kwargs,
            headers={"_schema_name": tenant.schema_name},
        )
        results.append(result)
    return results
