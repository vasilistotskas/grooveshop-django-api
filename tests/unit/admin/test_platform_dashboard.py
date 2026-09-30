"""Quick win #5 — the platform dashboard's estate table computes and
renders per-tenant revenue (completed-orders ``paid_amount`` sum),
mirroring the merchant dashboard's own
``admin/dashboard.py::_zone_b_ops_charts`` shape.

``_tenant_rows`` only populates ``orders``/``revenue`` when
``_schema_exists()`` is True (a half-provisioned tenant must read
"cannot tell", not a misleading 0 — see the module's own docstring).
The test suite disables per-schema routing (``DATABASE_ROUTERS = []``,
see ``tests/conftest.py``) so every table — including ``order_order``
— lives in the single physical ``public`` Postgres schema; a schema
just needs to EXIST (even empty) for ``tenant_context`` to fall
through to it via ``search_path``. A cheap ``CREATE SCHEMA`` (rolled
back automatically at the end of the test transaction) is enough to
exercise the real aggregation path without running per-tenant
migrations.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from django.db import connection

from admin.platform_dashboard import _tenant_rows, _tenants_table
from order.enum.status import PaymentStatus
from order.factories.order import OrderFactory
from tests.utils.staff import store_tenant

pytestmark = pytest.mark.django_db


def _store_with_empty_schema(schema_name: str):
    with connection.cursor() as cursor:
        cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_name}")
    return store_tenant(schema_name)


class TestTenantRowsRevenue:
    def test_revenue_sums_only_completed_orders(self):
        tenant = _store_with_empty_schema("revenue_rows_store")

        OrderFactory(
            payment_status=PaymentStatus.COMPLETED,
            paid_amount=30,
            num_order_items=0,
        )
        OrderFactory(
            payment_status=PaymentStatus.COMPLETED,
            paid_amount=20,
            num_order_items=0,
        )
        OrderFactory(
            payment_status=PaymentStatus.PENDING,
            paid_amount=999,
            num_order_items=0,
        )

        row = next(
            r for r in _tenant_rows() if r["schema"] == tenant.schema_name
        )

        assert row["orders"] == 3
        assert row["revenue"] == pytest.approx(50.0)

    def test_no_completed_orders_reports_zero_not_none(self):
        tenant = _store_with_empty_schema("revenue_rows_empty")

        OrderFactory(
            payment_status=PaymentStatus.PENDING,
            paid_amount=10,
            num_order_items=0,
        )

        row = next(
            r for r in _tenant_rows() if r["schema"] == tenant.schema_name
        )

        assert row["orders"] == 1
        assert row["revenue"] == 0.0

    def test_missing_schema_leaves_revenue_none(self):
        tenant = store_tenant("revenue_rows_no_schema")

        row = next(
            r for r in _tenant_rows() if r["schema"] == tenant.schema_name
        )

        assert row["orders"] is None
        assert row["revenue"] is None

    def test_unreadable_schema_logs_the_tenant_schema(self, caplog):
        tenant = _store_with_empty_schema("revenue_rows_unreadable")

        with (
            patch(
                "django_tenants.utils.tenant_context",
                side_effect=RuntimeError("schema unreadable"),
            ),
            caplog.at_level(logging.ERROR, logger="admin.platform_dashboard"),
        ):
            row = next(
                r for r in _tenant_rows() if r["schema"] == tenant.schema_name
            )

        assert row["revenue"] is None
        messages = [
            record.getMessage()
            for record in caplog.records
            if record.name == "admin.platform_dashboard"
        ]
        assert any(tenant.schema_name in message for message in messages)


class TestTenantsTableRevenueColumn:
    # Headers go through ``gettext`` and render in the active locale
    # (default ``el`` for the suite — see ``tests/conftest.py``); these
    # assertions check the literal English header text.
    pytestmark = pytest.mark.assert_english

    def test_renders_a_revenue_header_and_formatted_cell(self):
        table = _tenants_table(
            [
                {
                    "name": "Formatted Revenue Store",
                    "schema": "formatted_revenue_store",
                    "plan": "pro",
                    "is_active": True,
                    "suspended": False,
                    "domain": "formatted-revenue-store.example.com",
                    "orders": 5,
                    "revenue": 1234.5,
                }
            ]
        )

        assert "Revenue" in table["headers"]
        revenue_index = table["headers"].index("Revenue")
        assert table["rows"][0][revenue_index] == "€1.234,50"

    def test_unreadable_figures_render_a_dash_not_zero(self):
        """ "0 orders" would read as a real figure for a broken store."""
        table = _tenants_table(
            [
                {
                    "name": "No Revenue Data Store",
                    "schema": "no_revenue_data_store",
                    "plan": "trial",
                    "is_active": True,
                    "suspended": False,
                    "domain": "",
                    "orders": None,
                    "revenue": None,
                }
            ]
        )

        row = table["rows"][0]
        assert row[table["headers"].index("Orders")] == "—"
        assert row[table["headers"].index("Revenue")] == "—"
