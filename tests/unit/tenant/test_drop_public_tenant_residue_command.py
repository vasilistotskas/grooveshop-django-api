"""``drop_public_tenant_residue`` must be impossible to fire by accident.

This cleanup was first written as a migration. The ``tests`` lane strips
``DATABASE_ROUTERS``, so it is routerless and every table legitimately
lives in ``public`` there; with no router ``allow_migrate`` returns
``True``, the migration ran during test-database setup and dropped the
whole schema — 378 failures locally, and the same on every CI run.

So the two refusals below are the point of the command, not decoration.
This very test module runs in that routerless lane, which makes it the
exact environment that must be refused — and means these tests cannot
drop a real table even if the guards were removed, because the
table-name set is monkeypatched in the one test that executes a drop.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django_tenants.utils import get_public_schema_name

ROUTER_PATH = "django_tenants.routers.TenantSyncRouter"


def _run(**kwargs) -> str:
    out = StringIO()
    call_command("drop_public_tenant_residue", stdout=out, **kwargs)
    return out.getvalue()


class TestRefusals:
    def test_it_refuses_without_the_tenant_router(self, settings):
        """The regression that killed the migration. Routerless means
        tenant tables in public are correct, not residue."""
        settings.DATABASE_ROUTERS = []

        with pytest.raises(CommandError, match="router is not installed"):
            _run()

    def test_it_refuses_off_the_public_schema(self, settings, monkeypatch):
        settings.DATABASE_ROUTERS = [ROUTER_PATH]
        monkeypatch.setattr(connection, "schema_name", "webside", raising=False)

        with pytest.raises(CommandError, match="only ever operates"):
            _run()

    def test_the_routerless_refusal_fires_before_any_query(
        self, settings, monkeypatch
    ):
        """Precondition order matters: a refusal that ran after the
        table scan would still have listed — and could later drop —
        real tables."""
        settings.DATABASE_ROUTERS = []
        called = []
        monkeypatch.setattr(
            "tenant.app_labels.tenant_only_table_names",
            lambda: called.append(1) or set(),
        )

        with pytest.raises(CommandError):
            _run()

        assert called == [], "the table set was computed before refusing"


@pytest.mark.django_db
class TestDryRunIsTheDefault:
    def test_it_reports_without_dropping(self, settings, monkeypatch):
        settings.DATABASE_ROUTERS = [ROUTER_PATH]
        monkeypatch.setattr(
            connection, "schema_name", get_public_schema_name(), raising=False
        )
        public = get_public_schema_name()
        table = "unit_lane_fake_tenant_table"
        monkeypatch.setattr(
            "tenant.app_labels.tenant_only_table_names", lambda: {table}
        )

        with connection.cursor() as cursor:
            cursor.execute(
                f'create table {public}."{table}" (id serial primary key)'
            )

        output = _run()

        assert table in output
        assert "dry-run" in output
        with connection.cursor() as cursor:
            cursor.execute("select to_regclass(%s)", [f"{public}.{table}"])
            assert cursor.fetchone()[0] is not None, "dry run dropped it"

    def test_a_clean_schema_reports_nothing_to_do(self, settings, monkeypatch):
        settings.DATABASE_ROUTERS = [ROUTER_PATH]
        monkeypatch.setattr(
            connection, "schema_name", get_public_schema_name(), raising=False
        )
        monkeypatch.setattr(
            "tenant.app_labels.tenant_only_table_names",
            lambda: {"table_that_does_not_exist_anywhere"},
        )

        assert "nothing to do" in _run()


@pytest.mark.django_db
class TestExecution:
    def test_yes_drops_only_the_named_tables(self, settings, monkeypatch):
        """The set is monkeypatched to one throwaway table: in this
        routerless lane the real set names every table the suite needs."""
        settings.DATABASE_ROUTERS = [ROUTER_PATH]
        monkeypatch.setattr(
            connection, "schema_name", get_public_schema_name(), raising=False
        )
        public = get_public_schema_name()
        residue = "unit_lane_fake_tenant_table"
        decoy = "unit_lane_decoy_table"
        monkeypatch.setattr(
            "tenant.app_labels.tenant_only_table_names", lambda: {residue}
        )

        with connection.cursor() as cursor:
            for table in (residue, decoy):
                cursor.execute(
                    f'create table {public}."{table}" (id serial primary key)'
                )
            # A non-empty table is still dropped: rows here are
            # misrouted writes into the control-plane schema, which is
            # the defect being removed.
            cursor.execute(f'insert into {public}."{residue}" default values')

        output = _run(yes=True)

        assert "Dropped 1 table" in output
        with connection.cursor() as cursor:
            cursor.execute("select to_regclass(%s)", [f"{public}.{residue}"])
            assert cursor.fetchone()[0] is None, "residue survived"
            cursor.execute("select to_regclass(%s)", [f"{public}.{decoy}"])
            assert cursor.fetchone()[0] is not None, "a decoy was dropped"

    def test_row_counts_are_reported_before_dropping(
        self, settings, monkeypatch
    ):
        """The deploy record must name what was destroyed."""
        settings.DATABASE_ROUTERS = [ROUTER_PATH]
        monkeypatch.setattr(
            connection, "schema_name", get_public_schema_name(), raising=False
        )
        public = get_public_schema_name()
        table = "unit_lane_fake_tenant_table"
        monkeypatch.setattr(
            "tenant.app_labels.tenant_only_table_names", lambda: {table}
        )

        with connection.cursor() as cursor:
            cursor.execute(
                f'create table {public}."{table}" (id serial primary key)'
            )
            for _ in range(3):
                cursor.execute(f'insert into {public}."{table}" default values')

        output = _run(yes=True)

        assert "rows=3" in output
