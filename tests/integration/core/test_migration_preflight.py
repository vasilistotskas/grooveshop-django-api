"""``migration_preflight`` and the classifier behind it.

Most cases replay the real migration graph with no database: a deploy is
"everything applied except these migrations and their dependents", the
applied state is what the serving release's models look like, and the
pending plan is classified on top of it. That covers the incident this
exists for — v3.77.0's ``RenameField`` of the shared SEO columns, which
500'd the previous release for the length of the rollout — and the
correct two-step precedent (``page_config`` 0025/0026).

Synthetic migrations cover the shapes the history does not: a contract
``RunSQL`` with and without ``contract_of``, NOT NULL columns with and
without ``db_default``, ``accepted_downtime``, router scoping.

The command itself runs against the test database twice: fully
migrated (safe), and with ``blog`` rolled back one migration inside the
``TestCase`` transaction (blocked), which also pins the private
``MigrationExecutor._create_project_state`` the command relies on.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, migrations, models
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.state import ProjectState
from django.test import TestCase

from core.db.migration_safety import Finding, classify

MAX_LENGTH = 63


def _allow_everything(app_label, **hints):
    return True


def _deploy(*first_pending: tuple[str, str]):
    """(applied state, applied keys, pending migrations) for a deploy that
    applies ``first_pending`` and everything depending on them."""
    loader = MigrationLoader(None, ignore_no_migrations=True)
    graph = loader.graph
    pending: set[tuple[str, str]] = set()
    for key in first_pending:
        pending.update(graph.backwards_plan(key))
    order: list[tuple[str, str]] = []
    for leaf in graph.leaf_nodes():
        for key in graph.forwards_plan(leaf):
            if key not in order:
                order.append(key)
    applied = [key for key in order if key not in pending]
    state = graph.make_state(
        nodes=applied, at_end=True, real_apps=loader.unmigrated_apps
    )
    plan = [graph.nodes[key] for key in order if key in pending]
    return state, frozenset(applied), plan


def _classify(
    state, applied, plan, *, allow=_allow_everything
) -> list[Finding]:
    return classify(
        plan, state, allow=allow, applied=applied, max_length=MAX_LENGTH
    )


def _reasons(findings: list[Finding], migration: tuple[str, str]) -> list[str]:
    return [f.reason for f in findings if f.migration == migration]


def _migration(app_label: str, name: str, operations, **attributes):
    """A migration as a file declares it: attributes on the class."""
    cls = type(
        "Migration",
        (migrations.Migration,),
        {"operations": operations, **attributes},
    )
    return cls(name, app_label)


@pytest.fixture(scope="module")
def serving_at_blog_0034():
    return _deploy(("blog", "0035_seo_translated_fields"))


class TestTheV3770Incident:
    def test_the_shared_seo_rename_is_blocked(self, serving_at_blog_0034):
        findings = _classify(*serving_at_blog_0034)
        reasons = _reasons(findings, ("blog", "0035_seo_translated_fields"))

        for column in ("seo_title", "seo_description", "seo_keywords"):
            assert (
                f"removes column blog_blogpost.{column}, which the serving "
                "release still reads"
            ) in reasons

    def test_translated_columns_without_db_default_are_blocked(
        self, serving_at_blog_0034
    ):
        findings = _classify(*serving_at_blog_0034)
        reasons = _reasons(findings, ("blog", "0035_seo_translated_fields"))

        assert (
            "adds NOT NULL column blog_blogpost_translation.seo_title "
            "without db_default; the serving release's INSERTs do not "
            "name it"
        ) in reasons

    def test_dropping_columns_created_in_the_same_deploy_is_safe(
        self, serving_at_blog_0034
    ):
        findings = _classify(*serving_at_blog_0034)

        assert (
            _reasons(findings, ("blog", "0037_remove_shared_seo_fields")) == []
        )

    def test_the_history_table_drop_is_blocked(self):
        findings = _classify(
            *_deploy(("product", "0044_seo_translated_fields"))
        )
        reasons = _reasons(
            findings, ("product", "0046_remove_shared_seo_fields")
        )

        assert (
            "removes column product_historicalproduct.seo_title, which the "
            "serving release still reads"
        ) in reasons


class TestTheTwoStepPrecedent:
    def test_nullable_then_state_only_removal_is_safe(self):
        findings = _classify(
            *_deploy(("page_config", "0025_navigation_drop_json_from_state"))
        )

        assert (
            _reasons(
                findings,
                ("page_config", "0025_navigation_drop_json_from_state"),
            )
            == []
        )


DROP = migrations.RunSQL(
    "ALTER TABLE page_config_navigationmenu DROP COLUMN IF EXISTS items",
    reverse_sql=migrations.RunSQL.noop,
)
EXPAND = ("page_config", "0025_navigation_drop_json_from_state")


class TestContractMigrations:
    def _serving_after_expand(self):
        state, applied, _plan = _deploy(
            ("page_config", "0026_navigation_drop_json_columns")
        )
        return state, applied

    def test_a_contract_after_its_expand_was_deployed_is_safe(self):
        state, applied = self._serving_after_expand()
        contract = _migration(
            "page_config", "0999_drop", [DROP], contract_of=[EXPAND]
        )

        assert _classify(state, applied, [contract]) == []

    def test_a_contract_in_the_same_deploy_as_its_expand_is_blocked(self):
        state, applied, plan = _deploy(EXPAND)
        contract = _migration(
            "page_config", "0999_drop", [DROP], contract_of=[EXPAND]
        )

        reasons = _reasons(
            _classify(state, applied, [*plan, contract]),
            ("page_config", "0999_drop"),
        )

        assert reasons == [
            (
                "contract_of page_config.0025_navigation_drop_json_from_state "
                "is pending in this same deploy; deploy the release that "
                "contains it first"
            )
        ]

    def test_destructive_sql_without_contract_of_is_blocked(self):
        state, applied = self._serving_after_expand()

        findings = _classify(
            state, applied, [_migration("page_config", "0999_drop", [DROP])]
        )

        assert [f.reason for f in findings] == [
            (
                "RunSQL drops, renames or sets NOT NULL; declare contract_of "
                "naming the migration that took the columns out of state"
            )
        ]

    def test_an_unknown_contract_target_is_blocked(self):
        state, applied = self._serving_after_expand()
        contract = _migration(
            "page_config",
            "0999_drop",
            [DROP],
            contract_of=[("page_config", "0998_missing")],
        )

        assert [f.reason for f in _classify(state, applied, [contract])] == [
            (
                "contract_of page_config.0998_missing is not an applied "
                "migration of this schema"
            )
        ]

    def test_a_fresh_schema_runs_the_contract(self):
        contract = _migration(
            "page_config", "0999_drop", [DROP], contract_of=[EXPAND]
        )

        assert _classify(ProjectState(), frozenset(), [contract]) == []

    @pytest.mark.parametrize(
        "sql",
        [
            "CREATE INDEX foo ON page_config_navigationmenu (slot)",
            "ALTER TABLE page_config_navigationmenu ALTER slot DROP DEFAULT",
            "ALTER TABLE page_config_navigationmenu DROP CONSTRAINT foo",
            "ALTER TABLE page_config_navigationmenu RENAME CONSTRAINT a TO b",
        ],
    )
    def test_sql_that_removes_nothing_needs_no_declaration(self, sql):
        state, applied = self._serving_after_expand()
        migration = _migration(
            "page_config", "0999_sql", [migrations.RunSQL(sql)]
        )

        assert _classify(state, applied, [migration]) == []


def _serving_now():
    loader = MigrationLoader(None, ignore_no_migrations=True)
    nodes = loader.graph.leaf_nodes()
    return (
        loader.graph.make_state(
            nodes=nodes, at_end=True, real_apps=loader.unmigrated_apps
        ),
        frozenset(loader.graph.nodes),
    )


def _add(field: models.Field):
    return _migration(
        "country",
        "0999_add",
        [migrations.AddField("country", "postal_code_note", field)],
    )


class TestAddingAndTighteningColumns:
    def test_not_null_with_db_default_is_safe(self):
        # The shape of country/0011_country_postal_code_format.
        field = models.CharField(max_length=50, default="", db_default="")

        assert _classify(*_serving_now(), [_add(field)]) == []

    def test_nullable_is_safe(self):
        field = models.CharField(max_length=50, null=True)

        assert _classify(*_serving_now(), [_add(field)]) == []

    def test_not_null_with_only_a_python_default_is_blocked(self):
        field = models.CharField(max_length=50, default="")

        assert [
            f.reason for f in _classify(*_serving_now(), [_add(field)])
        ] == [
            (
                "adds NOT NULL column country_country.postal_code_note "
                "without db_default; the serving release's INSERTs do not "
                "name it"
            )
        ]

    def test_a_not_null_column_on_a_new_table_is_safe(self):
        created = _migration(
            "country",
            "0999_create",
            [
                migrations.CreateModel(
                    "Brand",
                    [
                        ("id", models.BigAutoField(primary_key=True)),
                        ("name", models.CharField(max_length=10)),
                    ],
                ),
                migrations.AddField(
                    "brand", "code", models.CharField(max_length=10)
                ),
            ],
        )

        assert _classify(*_serving_now(), [created]) == []

    def test_making_a_nullable_column_not_null_is_blocked(self):
        state, applied = _serving_now()
        # The serving release already has the column, nullable.
        migrations.AddField(
            "country",
            "postal_code_note",
            models.CharField(max_length=50, null=True),
        ).state_forwards("country", state)
        tighten = _migration(
            "country",
            "0999_tighten",
            [
                migrations.AlterField(
                    "country",
                    "postal_code_note",
                    models.CharField(max_length=50, default=""),
                )
            ],
        )

        findings = _classify(state, applied, [tighten])

        assert [f.reason for f in findings] == [
            (
                "makes column country_country.postal_code_note NOT NULL "
                "without db_default; the serving release may still write NULL"
            )
        ]


class TestRenamesAndDeletes:
    def test_a_state_only_rename_that_keeps_the_column_is_safe(self):
        rename = _migration(
            "country",
            "0999_rename",
            [
                migrations.SeparateDatabaseAndState(
                    state_operations=[
                        migrations.RenameField("country", "alpha_3", "iso3"),
                    ],
                    database_operations=[],
                )
            ],
        )

        assert _classify(*_serving_now(), [rename]) == []

    def test_a_real_rename_is_blocked(self):
        findings = _classify(
            *_serving_now(),
            [
                _migration(
                    "country",
                    "0999_rename",
                    [migrations.RenameField("country", "alpha_3", "iso3")],
                )
            ],
        )

        assert [f.reason for f in findings] == [
            (
                "removes column country_country.alpha_3, which the serving "
                "release still reads"
            )
        ]

    def test_a_dropped_table_is_reported_once_not_per_column(self):
        findings = _classify(
            *_serving_now(),
            [
                _migration(
                    "page_config",
                    "0999_delete",
                    [migrations.DeleteModel("NavigationMenu")],
                )
            ],
        )

        assert [f.reason for f in findings] == [
            (
                "removes table page_config_navigationmenu, which the serving "
                "release still reads"
            )
        ]

    def test_accepted_downtime_reports_without_blocking(self):
        findings = _classify(
            *_serving_now(),
            [
                _migration(
                    "country",
                    "0999_rename",
                    [migrations.RenameField("country", "alpha_3", "iso3")],
                    accepted_downtime="maintenance window 2026-10-01",
                )
            ],
        )

        assert len(findings) == 1
        assert not findings[0].blocking
        assert findings[0].accepted_downtime == "maintenance window 2026-10-01"

    def test_an_app_the_router_keeps_out_of_the_schema_is_ignored(self):
        findings = _classify(
            *_serving_now(),
            [
                _migration(
                    "country",
                    "0999_rename",
                    [migrations.RenameField("country", "alpha_3", "iso3")],
                )
            ],
            allow=lambda app_label, **hints: app_label != "country",
        )

        assert findings == []


def test_a_fresh_schema_passes_the_whole_history():
    loader = MigrationLoader(None, ignore_no_migrations=True)
    graph = loader.graph
    order: list[tuple[str, str]] = []
    for leaf in graph.leaf_nodes():
        for key in graph.forwards_plan(leaf):
            if key not in order:
                order.append(key)

    findings = _classify(
        ProjectState(real_apps=loader.unmigrated_apps),
        frozenset(),
        [graph.nodes[key] for key in order],
    )

    assert findings == []


class TestCommand(TestCase):
    def test_a_fully_migrated_database_is_safe(self):
        out = StringIO()

        call_command("migration_preflight", stdout=out)

        assert "public: 0 pending" in out.getvalue()
        assert "migration preflight: safe" in out.getvalue()

    def test_the_serving_state_is_the_one_migrate_starts_from(self):
        executor = MigrationExecutor(connection)

        state = executor._create_project_state(with_applied_migrations=True)

        assert isinstance(state, ProjectState)
        assert ("blog", "blogpost") in state.models

    def test_a_pending_destructive_migration_is_refused(self):
        # Transactional DDL: the TestCase rollback re-applies nothing and
        # leaves the worker's database at the latest migration.
        MigrationExecutor(connection).migrate(
            [("blog", "0036_copy_seo_into_translations")]
        )
        err = StringIO()

        with pytest.raises(CommandError, match="would break the release"):
            call_command("migration_preflight", stdout=StringIO(), stderr=err)

        assert "BLOCK blog.0037_remove_shared_seo_fields" in err.getvalue()
        assert "removes column blog_blogpost.shared_seo_title" in err.getvalue()
