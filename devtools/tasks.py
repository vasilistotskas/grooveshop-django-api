"""Scheduled upkeep for demo stores.

A demo store hands its login out in public, so it drifts: a prospect
places a cash-on-delivery order (which consumes stock), leaves a review,
comments on a post, fills a cart. None of that is wrong — it is the
point of giving them the account — but the next prospect should meet the
showroom, not the last visitor's mess.

Only tenants flagged ``is_demo`` are touched. The fan-out that picks
them lives in ``tenant/tasks.py`` with every other one — a beat entry
has to dispatch a ``tenant.tasks.fanout_*`` wrapper, which
``tests/unit/tenant/test_celery_fanout.py`` enforces, because a beat
entry pointed straight at a tenant-scoped task fires once in the
public schema and processes zero rows.
"""

from __future__ import annotations

import logging
from typing import Any

from django_tenants.utils import schema_context

from core import celery_app
from core.tasks import MonitoredTask

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
