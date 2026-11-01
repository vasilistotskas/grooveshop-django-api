"""A store's secrets must never be copied into ``HistoricalTenant``.

Every save of a ``Tenant`` writes a snapshot row, so an encrypted field that
is not in ``excluded_fields`` would put its plaintext in a table with no
retention or access policy of its own. The list is checked against the model
so a new secret cannot be forgotten.
"""

from __future__ import annotations

from core.db.encrypted_fields import EncryptedCharField
from tenant.models import Tenant


def test_no_encrypted_field_reaches_the_history_table():
    encrypted = {
        field.name
        for field in Tenant._meta.get_fields()
        if isinstance(field, EncryptedCharField)
    }
    historical = {
        field.name for field in Tenant.history.model._meta.get_fields()
    }

    assert {"resend_api_key", "resend_webhook_secret"} <= encrypted
    assert encrypted.isdisjoint(historical), encrypted & historical
