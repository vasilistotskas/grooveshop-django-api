"""Dashboard for the platform control plane.

Answers the questions an operator of the PLATFORM has — how many stores
exist, which are live, which are suspended, and is the scheduler
healthy — rather than a single store's sales figures, which belong on
that store's own admin.

Per-store numbers are read INSIDE each tenant's schema via
``tenant_context``. Reading them from the public schema would hit the
pre-multi-tenant legacy tables that still sit there, which is the same
trap that made tenant-only admin pages 500 before they were withheld.
"""

from __future__ import annotations

from typing import Any


def _tenant_rows() -> list[dict[str, Any]]:
    """One row per tenant, with its order count and revenue."""
    from django.apps import apps  # noqa: PLC0415
    from django_tenants.utils import (  # noqa: PLC0415
        get_public_schema_name,
        tenant_context,
    )

    Tenant = apps.get_model("tenant", "Tenant")
    public = get_public_schema_name()
    rows: list[dict[str, Any]] = []

    for tenant in Tenant.objects.exclude(schema_name=public).order_by("name"):
        row: dict[str, Any] = {
            "name": tenant.store_name or tenant.name,
            "schema": tenant.schema_name,
            "plan": tenant.plan,
            "is_active": tenant.is_active,
            "suspended": tenant.suspended_at is not None,
            "domain": "",
            "orders": None,
            "revenue": None,
        }
        primary = tenant.domains.filter(is_primary=True).first()
        if primary is not None:
            row["domain"] = primary.domain

        # A tenant whose schema is mid-provision has no tables yet.
        #
        # Check the schema EXISTS before counting rather than relying on
        # an exception: ``SET search_path`` to a missing schema does not
        # error, it silently falls through, and the count comes back 0.
        # The console would then report "0 orders" for a store that is
        # merely half-provisioned — indistinguishable from a real store
        # with no sales. Blank is the honest answer for "cannot tell".
        if _schema_exists(tenant.schema_name):
            try:
                with tenant_context(tenant):
                    from django.db.models import Sum  # noqa: PLC0415

                    from order.enum.status import PaymentStatus  # noqa: PLC0415

                    Order = apps.get_model("order", "Order")
                    row["orders"] = Order.objects.count()
                    # Same completed-orders Sum(paid_amount) shape as
                    # the per-store merchant dashboard
                    # (admin/dashboard.py::_zone_b_ops_charts) — only
                    # money that actually landed counts as revenue.
                    total = Order.objects.filter(
                        payment_status=PaymentStatus.COMPLETED
                    ).aggregate(total=Sum("paid_amount"))["total"]
                    row["revenue"] = float(total) if total is not None else 0.0
            except Exception:  # noqa: BLE001 - never hard-fail the console
                pass
        rows.append(row)
    return rows


def _schema_exists(schema_name: str) -> bool:
    from django.db import connection  # noqa: PLC0415

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
            [schema_name],
        )
        return cursor.fetchone() is not None


def _tenants_table(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Shape the estate for ``unfold/components/table.html``.

    Cells are plain strings or ``{"content", "class"}`` dicts — the
    component escapes ``content``, so a tenant-supplied store name can
    never inject markup into the control plane.
    """
    from django.utils.translation import gettext as _g  # noqa: PLC0415

    from admin.displays import money  # noqa: PLC0415

    table_rows = []
    for row in rows:
        if row["suspended"]:
            status = {
                "content": _g("Suspended"),
                "class": "text-red-600 dark:text-red-400 font-semibold",
            }
        elif not row["is_active"]:
            status = {"content": _g("Inactive"), "class": "text-base-500"}
        else:
            status = {
                "content": _g("Live"),
                "class": "text-green-600 dark:text-green-400 font-semibold",
            }

        table_rows.append(
            [
                row["name"],
                row["domain"] or "—",
                row["schema"],
                row["plan"] or "—",
                status,
                # Blank, not 0, when the schema could not be read — see
                # ``_tenant_rows``. "0 orders" would read as a real
                # figure for a half-provisioned store.
                "—" if row["orders"] is None else str(row["orders"]),
                "—" if row["revenue"] is None else money(row["revenue"]),
            ]
        )

    return {
        "headers": [
            _g("Store"),
            _g("Domain"),
            _g("Schema"),
            _g("Plan"),
            _g("Status"),
            _g("Orders"),
            _g("Revenue"),
        ],
        "rows": table_rows,
    }


def dashboard_callback(request, context):
    """Inject control-plane figures into the platform dashboard."""
    from django.apps import apps  # noqa: PLC0415
    from django_tenants.utils import get_public_schema_name  # noqa: PLC0415

    Tenant = apps.get_model("tenant", "Tenant")
    public = get_public_schema_name()
    stores = Tenant.objects.exclude(schema_name=public)

    rows = _tenant_rows()
    context.update(
        {
            "platform_tenant_count": stores.count(),
            "platform_active_count": stores.filter(
                is_active=True, suspended_at__isnull=True
            ).count(),
            "platform_suspended_count": stores.filter(
                suspended_at__isnull=False
            ).count(),
            "platform_tenants": rows,
            "platform_tenants_table": _tenants_table(rows),
            "platform_total_orders": sum(r["orders"] or 0 for r in rows),
        }
    )

    # Scheduler health — a control plane should surface a stalled beat
    # before a merchant notices their carrier polling stopped.
    try:
        PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
        context["platform_periodic_tasks"] = PeriodicTask.objects.filter(
            enabled=True
        ).count()
    except Exception:  # noqa: BLE001
        context["platform_periodic_tasks"] = None

    return context
