"""The contract release (``tenant`` 0052): carry over, then drop.

The test database was built by the migrations, so the plaintext columns
are already gone; each test puts them back (inside its own transaction,
rolled back at the end) to stand in for a production database that has
just finished release N.
"""

from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection
from extra_settings.models import Setting

from core.db.encrypted_fields import decrypt_value
from tenant.models import Tenant
from tests.utils.staff import store_tenant

pytestmark = pytest.mark.django_db

contract = import_module("tenant.migrations.0052_drop_plaintext_tenant_secrets")
expand = import_module("tenant.migrations.0050_encrypt_tenant_secrets")

SCHEMA_EDITOR = SimpleNamespace(connection=connection)


def _columns() -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'tenant_tenant'"
        )
        return {row[0] for row in cursor.fetchall()}


@pytest.fixture
def old_columns():
    with connection.cursor() as cursor:
        cursor.execute(contract.RESTORE_SQL)


def _write(tenant: Tenant, column: str, value: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE tenant_tenant SET {column} = %s WHERE id = %s",
            [value, tenant.pk],
        )


def test_the_test_database_has_no_plaintext_columns():
    assert not set(expand.SECRET_FIELDS) & _columns()


def test_the_restore_sql_brings_back_every_old_column(old_columns):
    assert set(expand.SECRET_FIELDS) <= _columns()


def test_dropping_leaves_the_encrypted_columns_and_the_model_working(
    old_columns,
):
    tenant = store_tenant("contract_drop", stripe_secret_key="sk_live_keep")
    with connection.cursor() as cursor:
        cursor.execute(contract.DROP_SQL)

    columns = _columns()
    assert not set(expand.SECRET_FIELDS) & columns
    assert {f"{name}_encrypted" for name in expand.SECRET_FIELDS} <= columns
    assert Tenant.objects.get(pk=tenant.pk).stripe_secret_key == "sk_live_keep"


def test_a_value_the_previous_release_wrote_is_encrypted_first(old_columns):
    tenant = store_tenant("contract_late_write")
    _write(tenant, "chat_api_key", "written-by-the-old-pods")

    contract.carry_over_late_writes(apps, SCHEMA_EDITOR)

    assert (
        Tenant.objects.get(pk=tenant.pk).chat_api_key
        == "written-by-the-old-pods"
    )


def test_a_value_set_through_the_new_release_is_never_overwritten(
    old_columns,
):
    tenant = store_tenant("contract_keep_new", chat_api_key="set-since")
    _write(tenant, "chat_api_key", "stale-plaintext")

    contract.carry_over_late_writes(apps, SCHEMA_EDITOR)

    stored = Tenant.objects.get(pk=tenant.pk).chat_api_key
    assert stored == "set-since"
    assert decrypt_value(_raw_encrypted(tenant)) == "set-since"


def _raw_encrypted(tenant: Tenant) -> str:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT chat_api_key_encrypted FROM tenant_tenant WHERE id = %s",
            [tenant.pk],
        )
        return cursor.fetchone()[0]


class TestMyDataSettings:
    @pytest.fixture
    def settings_rows(self):
        for name, value in (
            ("MYDATA_USER_ID", "late-user"),
            ("MYDATA_SUBSCRIPTION_KEY", "late-key"),
        ):
            Setting.objects.update_or_create(
                name=name,
                defaults={"value_type": "string", "value_string": value},
            )

    def test_a_late_setting_is_copied_then_the_rows_are_deleted(
        self, old_columns, settings_rows
    ):
        # ``public`` is the one schema the test database has an
        # ``extra_settings_setting`` table in.
        tenant = store_tenant("public")

        contract.carry_over_late_writes(apps, SCHEMA_EDITOR)

        loaded = Tenant.objects.get(pk=tenant.pk)
        assert loaded.mydata_user_id == "late-user"
        assert loaded.mydata_subscription_key == "late-key"
        assert not Setting.objects.filter(
            name__in=["MYDATA_USER_ID", "MYDATA_SUBSCRIPTION_KEY"]
        ).exists()

    def test_other_settings_are_left_alone(self, old_columns, settings_rows):
        store_tenant("public")
        Setting.objects.update_or_create(
            name="MYDATA_ENABLED",
            defaults={"value_type": "bool", "value_bool": True},
        )

        contract.carry_over_late_writes(apps, SCHEMA_EDITOR)

        assert Setting.objects.filter(name="MYDATA_ENABLED").exists()


def test_the_contract_names_the_expand_migrations_it_finishes():
    assert contract.Migration.contract_of == [
        ("tenant", "0050_encrypt_tenant_secrets"),
        ("tenant", "0051_copy_mydata_credentials"),
    ]
