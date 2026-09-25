"""``migration_preflight`` honours the real ``TenantSyncRouter``.

A pending ``blog`` drop matters in a tenant schema, where the serving
release reads ``blog_blogpost``, and not in ``public``, which has no such
table. A ``country`` rename is the other way round: ``country`` is a
SHARED_APPS table. The main lane strips ``DATABASE_ROUTERS``, so only
this lane can show the classifier asks the router per schema, as
``Operation.allow_migrate_model`` does when ``migrate_schemas`` runs.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection, migrations, router
from django.db.migrations.loader import MigrationLoader
from django_tenants.utils import get_public_schema_name, schema_context

from core.db.migration_safety import classify

BLOG_CONTRACT = ("blog", "0037_remove_shared_seo_fields")


def _serving_before_blog_contract():
    loader = MigrationLoader(None, ignore_no_migrations=True)
    nodes = [
        node for node in loader.graph.leaf_nodes() if node[0] != "blog"
    ] + [("blog", "0036_copy_seo_into_translations")]
    state = loader.graph.make_state(
        nodes=nodes, at_end=True, real_apps=loader.unmigrated_apps
    )
    applied = frozenset(
        key for node in nodes for key in loader.graph.forwards_plan(node)
    )
    return state, applied, loader.graph.nodes[BLOG_CONTRACT]


def _country_rename():
    cls = type(
        "Migration",
        (migrations.Migration,),
        {"operations": [migrations.RenameField("country", "alpha_3", "iso3")]},
    )
    return cls("0999_rename", "country")


def _findings_in(schema_name: str) -> set[tuple[str, str]]:
    state, applied, blog_contract = _serving_before_blog_contract()
    with schema_context(schema_name):
        findings = classify(
            [blog_contract, _country_rename()],
            state,
            allow=lambda app_label, **hints: router.allow_migrate(
                connection.alias, app_label, **hints
            ),
            applied=applied,
            max_length=connection.ops.max_name_length(),
        )
    return {finding.migration for finding in findings}


@pytest.mark.django_db
def test_a_tenant_app_drop_blocks_only_tenant_schemas(mt_tenant):
    assert _findings_in(mt_tenant.schema_name) == {BLOG_CONTRACT}


@pytest.mark.django_db
def test_a_shared_app_rename_blocks_only_public(mt_tenant):
    assert _findings_in(get_public_schema_name()) == {
        ("country", "0999_rename")
    }


@pytest.mark.django_db
def test_every_schema_is_checked_and_a_migrated_database_passes(mt_tenant):
    out = StringIO()

    call_command("migration_preflight", stdout=out)

    lines = out.getvalue().splitlines()
    assert lines[0] == f"{get_public_schema_name()}: 0 pending"
    assert f"{mt_tenant.schema_name}: 0 pending" in lines
    assert lines[-1] == "migration preflight: safe"
