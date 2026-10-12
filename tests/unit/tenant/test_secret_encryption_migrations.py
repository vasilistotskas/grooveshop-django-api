"""The expand release's data steps (``tenant`` 0050 and 0051).

The test database is built by the migrations themselves, so the schema
is the real one: the old plaintext columns still exist next to the new
``*_encrypted`` ones. The functions are called directly, on rows put in
the state the production database is in when the migration runs.
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

expand = import_module("tenant.migrations.0050_encrypt_tenant_secrets")
copy_mydata = import_module("tenant.migrations.0051_copy_mydata_credentials")
contract = import_module("tenant.migrations.0052_drop_plaintext_tenant_secrets")

SCHEMA_EDITOR = SimpleNamespace(connection=connection)


@pytest.fixture(autouse=True)
def _plaintext_columns():
    """The contract migration (0052) dropped the plaintext columns from the
    test database; the expand step runs while they still exist. The DDL is
    rolled back with the test's transaction."""
    with connection.cursor() as cursor:
        cursor.execute(contract.RESTORE_SQL)


def _write(tenant: Tenant, column: str, value: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE tenant_tenant SET {column} = %s WHERE id = %s",
            [value, tenant.pk],
        )


def _read(tenant: Tenant, column: str) -> str:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {column} FROM tenant_tenant WHERE id = %s", [tenant.pk]
        )
        return cursor.fetchone()[0]


class TestEncryptExistingSecrets:
    def test_every_plaintext_column_is_encrypted_into_its_new_column(self):
        tenant = store_tenant("mig_encrypt_all")
        for name in expand.SECRET_FIELDS:
            _write(tenant, name, f"plain-{name}")

        expand.encrypt_existing_secrets(apps, SCHEMA_EDITOR)

        for name in expand.SECRET_FIELDS:
            stored = _read(tenant, f"{name}_encrypted")
            assert f"plain-{name}" not in stored
            assert decrypt_value(stored) == f"plain-{name}"
        loaded = Tenant.objects.get(pk=tenant.pk)
        for name in expand.SECRET_FIELDS:
            assert getattr(loaded, name) == f"plain-{name}"

    def test_the_plaintext_columns_are_left_for_the_old_release(self):
        tenant = store_tenant("mig_keeps_old")
        _write(tenant, "stripe_secret_key", "sk_live_old")

        expand.encrypt_existing_secrets(apps, SCHEMA_EDITOR)

        assert _read(tenant, "stripe_secret_key") == "sk_live_old"

    def test_a_rerun_changes_nothing(self):
        tenant = store_tenant("mig_idempotent")
        _write(tenant, "chat_api_key", "chat-old")

        expand.encrypt_existing_secrets(apps, SCHEMA_EDITOR)
        first = _read(tenant, "chat_api_key_encrypted")
        expand.encrypt_existing_secrets(apps, SCHEMA_EDITOR)

        assert _read(tenant, "chat_api_key_encrypted") == first

    def test_a_value_set_since_is_never_overwritten(self):
        tenant = store_tenant("mig_no_overwrite", chat_api_key="chat-new")
        _write(tenant, "chat_api_key", "chat-stale")

        expand.encrypt_existing_secrets(apps, SCHEMA_EDITOR)

        assert Tenant.objects.get(pk=tenant.pk).chat_api_key == "chat-new"

    def test_empty_columns_stay_empty(self):
        tenant = store_tenant("mig_empty")

        expand.encrypt_existing_secrets(apps, SCHEMA_EDITOR)

        for name in expand.SECRET_FIELDS:
            assert _read(tenant, f"{name}_encrypted") == ""


class TestCopyMyDataCredentials:
    @pytest.fixture
    def settings_rows(self):
        for name, value in (
            ("MYDATA_USER_ID", "aade-user"),
            ("MYDATA_SUBSCRIPTION_KEY", "aade-key"),
        ):
            Setting.objects.update_or_create(
                name=name,
                defaults={"value_type": "string", "value_string": value},
            )

    def test_the_setting_values_land_on_the_tenant_row(self, settings_rows):
        # ``public`` is the one schema the test database has an
        # ``extra_settings_setting`` table in.
        tenant = store_tenant("public")

        copy_mydata.copy_mydata_credentials(apps, SCHEMA_EDITOR)

        loaded = Tenant.objects.get(pk=tenant.pk)
        assert loaded.mydata_user_id == "aade-user"
        assert loaded.mydata_subscription_key == "aade-key"
        stored = _read(tenant, "mydata_subscription_key")
        assert "aade-key" not in stored
        assert decrypt_value(stored) == "aade-key"

    def test_a_rerun_never_overwrites_what_an_operator_set(self, settings_rows):
        tenant = store_tenant(
            "public",
            mydata_user_id="typed-since",
            mydata_subscription_key="typed-key",
        )

        copy_mydata.copy_mydata_credentials(apps, SCHEMA_EDITOR)

        loaded = Tenant.objects.get(pk=tenant.pk)
        assert loaded.mydata_user_id == "typed-since"
        assert loaded.mydata_subscription_key == "typed-key"

    def test_the_setting_rows_stay_for_the_release_still_serving(
        self, settings_rows
    ):
        store_tenant("public")

        copy_mydata.copy_mydata_credentials(apps, SCHEMA_EDITOR)

        assert Setting.objects.filter(name="MYDATA_USER_ID").exists()

    def test_a_store_without_a_schema_is_skipped(self, settings_rows):
        tenant = store_tenant("mig_never_provisioned")

        copy_mydata.copy_mydata_credentials(apps, SCHEMA_EDITOR)

        assert Tenant.objects.get(pk=tenant.pk).mydata_user_id == ""
