"""Regression coverage for ``user/migrations/0026_admin_log_public_actor_fk``.

An admin-log row records a PUBLIC-schema actor (platform staff) acting
on a TENANT-schema object, and Postgres cannot express a cross-schema
foreign key. That migration drops the ``user_id`` FK constraint in
every TENANT schema (public keeps a real one — there the actor
genuinely is a local row) while leaving ``content_type_id`` alone,
since a tenant's content-type id space differs from public's and that
FK must stay enforced. The main suite runs in a single unified schema
where "cross-schema" doesn't exist, so this constraint asymmetry is
untestable there.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django_tenants.utils import schema_context

_FIND_FK = """
    SELECT con.conname
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
    WHERE rel.relname = 'django_admin_log'
      AND con.contype = 'f'
      AND nsp.nspname = %s
      AND pg_get_constraintdef(con.oid) LIKE %s
"""


def _has_fk_on(schema_name: str, column: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(_FIND_FK, [schema_name, f"FOREIGN KEY ({column})%"])
        return cursor.fetchone() is not None


@pytest.mark.django_db
def test_tenant_schema_admin_log_has_no_actor_fk_but_keeps_content_type_fk(
    mt_tenant,
):
    with schema_context(mt_tenant.schema_name):
        assert not _has_fk_on(mt_tenant.schema_name, "user_id"), (
            "django_admin_log.user_id still has a real FK constraint in "
            "the tenant schema — a fresh tenant's first admin save by a "
            "public-schema actor will raise ForeignKeyViolation"
        )
        assert _has_fk_on(mt_tenant.schema_name, "content_type_id"), (
            "django_admin_log.content_type_id lost its FK constraint in "
            "the tenant schema — that one must stay enforced"
        )


@pytest.mark.django_db
def test_public_schema_admin_log_keeps_the_real_actor_fk(mt_public_tenant):
    with schema_context(mt_public_tenant.schema_name):
        assert _has_fk_on(mt_public_tenant.schema_name, "user_id"), (
            "django_admin_log.user_id lost its FK constraint in public — "
            "there the actor is genuinely a local row and integrity "
            "should stay enforced"
        )
