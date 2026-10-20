"""``tenant`` 0058: the staff second factor's table exists in public.

Production's public schema recorded the ``mfa`` migrations from before
multi-tenancy, while the cutover cleanup had dropped the table. Adding
``allauth.mfa`` to ``SHARED_APPS`` then created nothing, and every
platform superuser admin login answered 500 on the missing relation.
"""

from __future__ import annotations

from importlib import import_module

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from admin import staff_mfa
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

migration = import_module(
    "tenant.migrations.0058_restore_public_mfa_authenticator"
)

TABLE = "mfa_authenticator"


def _tables() -> list[str]:
    with connection.cursor() as cursor:
        return connection.introspection.table_names(cursor)


def _run_migration() -> None:
    state = MigrationExecutor(connection).loader.project_state(
        ("tenant", "0058_restore_public_mfa_authenticator")
    )
    with connection.schema_editor() as editor:
        migration.create_missing_authenticator_table(state.apps, editor)


def test_recreates_a_table_that_was_dropped_but_still_recorded():
    with connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE {TABLE} CASCADE")
    assert TABLE not in _tables()

    _run_migration()

    assert TABLE in _tables()
    superuser = UserAccountFactory(is_superuser=True, is_staff=True)
    assert staff_mfa.is_enrolled(superuser) is False


def test_leaves_an_existing_table_and_its_rows_alone():
    superuser = UserAccountFactory(is_superuser=True, is_staff=True)
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {TABLE} (user_id, type, data, created_at) "
            "VALUES (%s, 'totp', '{}', now())",
            [superuser.pk],
        )

    _run_migration()

    assert staff_mfa.is_enrolled(superuser) is True
