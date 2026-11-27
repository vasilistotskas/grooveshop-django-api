"""Backfill existing ``Tenant.chat_api_key`` rows into the AI profile.

Before per-store AI profiles, every store with ``chat_api_key`` spoke to
Google Gemini's OpenAI-compatible endpoint using ``gemini-3.5-flash-lite``
(the platform gateway's ``CHAT_BASE_URL`` and ``CHAT_MODEL``). Copy each
existing encrypted key into ``ai_api_key`` alongside that provider and
model so no live store loses its chat assistant when the gateway switches
to reading ``aiProfile``.

Idempotent: only rows with a non-empty ``chat_api_key_encrypted`` and all
three AI profile columns still empty are updated.
"""

from __future__ import annotations

from django.db import migrations

LEGACY_PROVIDER = "gemini"
LEGACY_MODEL = "gemini-3.5-flash-lite"


def backfill_ai_profile_from_chat_key(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE tenant_tenant
            SET ai_provider = %s,
                ai_model = %s,
                ai_api_key = chat_api_key_encrypted
            WHERE chat_api_key_encrypted <> ''
              AND ai_provider = ''
              AND ai_model = ''
              AND ai_api_key = ''
            """,
            [LEGACY_PROVIDER, LEGACY_MODEL],
        )


class Migration(migrations.Migration):
    dependencies = [
        ("tenant", "0076_tenant_ai_profile"),
    ]

    operations = [
        migrations.RunPython(
            backfill_ai_profile_from_chat_key,
            migrations.RunPython.noop,
        ),
    ]
