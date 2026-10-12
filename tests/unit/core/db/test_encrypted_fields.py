"""``EncryptedCharField``: authenticated encryption at rest.

The field is exercised through ``Tenant``, whose secrets it protects, so
the assertions about what the DATABASE holds read the real column.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from django.db import connection

from core.db.encrypted_fields import (
    DecryptionError,
    decrypt_value,
    encrypt_value,
    rotate_value,
)
from tenant.models import Tenant
from tests.utils.staff import store_tenant

pytestmark = pytest.mark.django_db

SECRET = "sk_live_not-a-real-key"


def _raw(tenant: Tenant, column: str) -> str:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {column} FROM tenant_tenant WHERE id = %s",
            [tenant.pk],
        )
        return cursor.fetchone()[0]


@pytest.fixture
def two_keys():
    return Fernet.generate_key().decode(), Fernet.generate_key().decode()


def test_a_value_round_trips():
    tenant = store_tenant("enc_round_trip", stripe_secret_key=SECRET)
    assert Tenant.objects.get(pk=tenant.pk).stripe_secret_key == SECRET


def test_the_column_holds_ciphertext_not_the_plaintext():
    tenant = store_tenant("enc_at_rest", stripe_secret_key=SECRET)
    stored = _raw(tenant, "stripe_secret_key_encrypted")
    assert stored
    assert SECRET not in stored
    assert stored != SECRET
    assert decrypt_value(stored) == SECRET


def test_the_empty_string_stays_empty():
    tenant = store_tenant("enc_empty")
    assert _raw(tenant, "stripe_secret_key_encrypted") == ""
    assert Tenant.objects.get(pk=tenant.pk).stripe_secret_key == ""
    assert encrypt_value("") == ""
    assert decrypt_value("") == ""
    assert rotate_value("") == ""


def test_exclude_empty_still_filters_on_the_database():
    configured = store_tenant("enc_has_token", cloudflare_api_token="tok")
    store_tenant("enc_no_token")
    found = Tenant.objects.exclude(cloudflare_api_token="").values_list(
        "pk", flat=True
    )
    assert list(found) == [configured.pk]


def test_the_same_value_encrypts_differently_each_time():
    assert encrypt_value(SECRET) != encrypt_value(SECRET)


def test_a_save_without_update_fields_keeps_the_value():
    tenant = store_tenant("enc_resave", stripe_secret_key=SECRET)
    loaded = Tenant.objects.get(pk=tenant.pk)
    loaded.store_name = "Renamed"
    loaded.save()
    assert Tenant.objects.get(pk=tenant.pk).stripe_secret_key == SECRET


def test_a_value_encrypted_under_an_old_key_is_still_read(settings, two_keys):
    old, new = two_keys
    settings.FIELD_ENCRYPTION_KEYS = [old]
    tenant = store_tenant("enc_two_keys", stripe_secret_key=SECRET)
    settings.FIELD_ENCRYPTION_KEYS = [new, old]
    assert Tenant.objects.get(pk=tenant.pk).stripe_secret_key == SECRET


def test_new_writes_use_the_first_key(settings, two_keys):
    old, new = two_keys
    settings.FIELD_ENCRYPTION_KEYS = [new, old]
    token = encrypt_value(SECRET)
    # Only the first key opens it: drop the old one and it still reads,
    # drop the new one and it does not.
    settings.FIELD_ENCRYPTION_KEYS = [new]
    assert decrypt_value(token) == SECRET
    settings.FIELD_ENCRYPTION_KEYS = [old]
    with pytest.raises(DecryptionError):
        decrypt_value(token)


def test_rotation_moves_a_token_to_the_first_key(settings, two_keys):
    old, new = two_keys
    settings.FIELD_ENCRYPTION_KEYS = [old]
    token = encrypt_value(SECRET)
    settings.FIELD_ENCRYPTION_KEYS = [new, old]
    rotated = rotate_value(token)
    assert rotated != token
    settings.FIELD_ENCRYPTION_KEYS = [new]
    assert decrypt_value(rotated) == SECRET


def test_rotating_a_current_token_changes_nothing(settings, two_keys):
    old, new = two_keys
    settings.FIELD_ENCRYPTION_KEYS = [new, old]
    token = encrypt_value(SECRET)
    assert rotate_value(token) == token


def test_a_wrong_key_fails_loudly_on_load(settings, two_keys):
    old, new = two_keys
    settings.FIELD_ENCRYPTION_KEYS = [old]
    tenant = store_tenant("enc_wrong_key", stripe_secret_key=SECRET)
    settings.FIELD_ENCRYPTION_KEYS = [new]
    with pytest.raises(DecryptionError) as raised:
        Tenant.objects.get(pk=tenant.pk)
    assert SECRET not in str(raised.value)


def test_a_tampered_token_fails_loudly():
    token = encrypt_value(SECRET)
    flipped = token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1]
    with pytest.raises(DecryptionError):
        decrypt_value(flipped)


def test_plaintext_in_the_encrypted_column_is_refused():
    """A value written around the field is never handed back as if it were
    a credential."""
    tenant = store_tenant("enc_plaintext_column")
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE tenant_tenant SET stripe_secret_key_encrypted = %s "
            "WHERE id = %s",
            [SECRET, tenant.pk],
        )
    with pytest.raises(DecryptionError):
        Tenant.objects.get(pk=tenant.pk)
