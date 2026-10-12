"""``rotate_tenant_secrets``: move every stored secret to the primary key."""

from __future__ import annotations

from io import StringIO

import pytest
from cryptography.fernet import Fernet
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection

from tenant.models import Tenant
from tests.utils.staff import store_tenant

pytestmark = pytest.mark.django_db


def _raw(tenant: Tenant, column: str) -> str:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {column} FROM tenant_tenant WHERE id = %s",
            [tenant.pk],
        )
        return cursor.fetchone()[0]


@pytest.fixture
def rotated_setup(settings):
    old = Fernet.generate_key().decode()
    new = Fernet.generate_key().decode()
    settings.FIELD_ENCRYPTION_KEYS = [old]
    tenant = store_tenant(
        "rotate_one",
        stripe_secret_key="sk_live_rotate",
        chat_api_key="chat-rotate",
    )
    settings.FIELD_ENCRYPTION_KEYS = [new, old]
    return tenant, old, new


def _run() -> str:
    out = StringIO()
    call_command("rotate_tenant_secrets", stdout=out)
    return out.getvalue()


def test_secrets_end_up_readable_with_only_the_new_key(settings, rotated_setup):
    tenant, _old, new = rotated_setup
    output = _run()
    assert "Re-encrypted 2 value(s) across 1 tenant(s)" in output

    settings.FIELD_ENCRYPTION_KEYS = [new]
    loaded = Tenant.objects.get(pk=tenant.pk)
    assert loaded.stripe_secret_key == "sk_live_rotate"
    assert loaded.chat_api_key == "chat-rotate"


def test_a_second_run_writes_nothing(rotated_setup):
    tenant, _old, _new = rotated_setup
    _run()
    after_first = _raw(tenant, "stripe_secret_key_encrypted")
    output = _run()
    assert "Re-encrypted 0 value(s) across 0 tenant(s)" in output
    assert _raw(tenant, "stripe_secret_key_encrypted") == after_first


def test_only_counts_are_printed(rotated_setup):
    output = _run()
    assert "sk_live_rotate" not in output
    assert "chat-rotate" not in output


def test_a_token_no_key_opens_aborts_without_writing(settings, rotated_setup):
    tenant, _old, _new = rotated_setup
    before = _raw(tenant, "stripe_secret_key_encrypted")
    settings.FIELD_ENCRYPTION_KEYS = [Fernet.generate_key().decode()]
    with pytest.raises(CommandError, match="stripe_secret_key"):
        _run()
    assert _raw(tenant, "stripe_secret_key_encrypted") == before
