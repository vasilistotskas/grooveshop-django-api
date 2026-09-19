"""Scheduled upkeep for demo stores.

A demo store hands its login out in public, so it drifts: a prospect
places a cash-on-delivery order (which consumes stock), leaves a review,
comments on a post, fills a cart. None of that is wrong — it is the
point of giving them the account — but the next prospect should meet the
showroom, not the last visitor's mess.

Only tenants flagged ``is_demo`` are touched. The fan-out below is
deliberately NOT ``tenant.celery.run_for_all_tenants``: that helper
dispatches to every active tenant, and a reset that ran against a real
merchant would delete their customer's data. The filter is the safety
property here, so it is written out rather than parameterised.
"""

from __future__ import annotations

import logging
from typing import Any

from django_tenants.utils import schema_context

from core import celery_app
from core.tasks import MonitoredTask
from tenant.celery import TenantTask

logger = logging.getLogger(__name__)


@celery_app.task(base=MonitoredTask)
def reset_demo_store_task(schema_name: str) -> dict[str, Any]:
    """Put one demo store's shared accounts back to their fixtures."""
    from devtools.demo_account import reset_demo_account

    with schema_context(schema_name):
        report = reset_demo_account()
    logger.info(
        "Reset demo store %s: %s",
        schema_name,
        ", ".join(f"{key}={value}" for key, value in sorted(report.items())),
    )
    return {"schema": schema_name, **report}


@celery_app.task(base=TenantTask)
def fanout_reset_demo_stores() -> list[dict[str, str]]:
    """Dispatch a reset for every active, unsuspended demo tenant."""
    from tenant.models import Tenant

    tenants = Tenant.objects.filter(
        is_active=True, is_demo=True, suspended_at__isnull=True
    ).exclude(schema_name="public")

    dispatched: list[dict[str, str]] = []
    for tenant in tenants:
        result = reset_demo_store_task.apply_async(
            args=[tenant.schema_name],
            ignore_result=reset_demo_store_task.ignore_result,
        )
        dispatched.append(
            {"schema": tenant.schema_name, "task_id": str(result.id)}
        )
    if not dispatched:
        logger.info("No demo tenants to reset")
    return dispatched
