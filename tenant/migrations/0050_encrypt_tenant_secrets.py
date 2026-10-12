"""Encrypt the tenant secrets at rest: expand step (release N).

``Tenant``'s thirteen secret columns held plaintext. The model now maps
each attribute onto a NEW ``<name>_encrypted`` text column through
``EncryptedCharField``; the attribute names callers use are unchanged.

The release still serving during the PreSync rollout reads and writes
the OLD plaintext columns (``docs/migrations.md``), so this migration
only adds:

1. every ``<name>_encrypted`` column, ``NOT NULL DEFAULT ''``;
2. ``DEFAULT ''`` on every old column, because the new code no longer
   names them and its INSERTs would otherwise fail their NOT NULL;
3. every existing plaintext value, encrypted into its new column (needs
   ``FIELD_ENCRYPTION_KEYS``, which the PreSync hook receives from the
   same secret as the application). A re-run only fills the columns that
   are still empty;
4. the model STATE switch: each field now points at the new column. The
   old columns stay physically until the contract migration
   (``0052_drop_plaintext_tenant_secrets``) drops them.

Also adds the per-tenant myDATA credentials (``mydata_user_id``,
``mydata_subscription_key``); ``0051`` fills them from the stores'
``extra_settings`` rows.
"""

import core.db.encrypted_fields
from django.db import migrations, models

from core.db.encrypted_fields import encrypt_value

SECRET_FIELDS = (
    "stripe_secret_key",
    "cloudflare_api_token",
    "meta_capi_access_token",
    "viva_wallet_api_key",
    "viva_wallet_client_secret",
    "viva_wallet_webhook_verification_key",
    "acs_api_key",
    "acs_company_password",
    "acs_user_password",
    "box_now_client_secret",
    "box_now_webhook_secret",
    "acp_bearer_token",
    "chat_api_key",
)

ADD_COLUMNS_SQL = "\n".join(
    f"ALTER TABLE tenant_tenant ADD COLUMN IF NOT EXISTS "
    f"{name}_encrypted text NOT NULL DEFAULT '';\n"
    f"ALTER TABLE tenant_tenant ALTER COLUMN {name} SET DEFAULT '';"
    for name in SECRET_FIELDS
)
# The old columns keep their NOT NULL (the release that still reads them
# needs it) and lose only the default the new code never supplies. The
# new columns are dropped on the way back; the old defaults are left in
# place, which is harmless.
REVERSE_SQL = "\n".join(
    f"ALTER TABLE tenant_tenant DROP COLUMN IF EXISTS {name}_encrypted;"
    for name in SECRET_FIELDS
)


def encrypt_existing_secrets(apps, schema_editor):
    """Encrypt each plaintext secret into its still-empty new column."""
    with schema_editor.connection.cursor() as cursor:
        for name in SECRET_FIELDS:
            cursor.execute(
                f"SELECT id, {name} FROM tenant_tenant "
                f"WHERE {name} <> '' AND {name}_encrypted = ''"
            )
            for pk, plaintext in cursor.fetchall():
                cursor.execute(
                    f"UPDATE tenant_tenant SET {name}_encrypted = %s "
                    "WHERE id = %s",
                    [encrypt_value(plaintext), pk],
                )


class Migration(migrations.Migration):
    dependencies = [
        ("tenant", "0049_remove_warn_unprinted_acs_vouchers_beat"),
    ]

    operations = [
        migrations.AddField(
            model_name='historicaltenant',
            name='mydata_user_id',
            field=models.CharField(blank=True, db_default='', default='', help_text="The aade-user-id of the store's TAXISnet myDATA developer account. No platform fallback — empty disables myDATA.", max_length=64, verbose_name='myDATA user ID'),
        ),
        migrations.AddField(
            model_name='tenant',
            name='mydata_subscription_key',
            field=core.db.encrypted_fields.EncryptedCharField(blank=True, db_default='', default='', help_text='The Ocp-Apim-Subscription-Key from the myDATA developer portal. Secret. No platform fallback — empty disables myDATA.', max_length=255, verbose_name='myDATA subscription key'),
        ),
        migrations.AddField(
            model_name='tenant',
            name='mydata_user_id',
            field=models.CharField(blank=True, db_default='', default='', help_text="The aade-user-id of the store's TAXISnet myDATA developer account. No platform fallback — empty disables myDATA.", max_length=64, verbose_name='myDATA user ID'),
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(ADD_COLUMNS_SQL, reverse_sql=REVERSE_SQL),
                migrations.RunPython(
                    encrypt_existing_secrets, migrations.RunPython.noop
                ),
            ],
            state_operations=[
                migrations.AlterField(
                    model_name='tenant',
                    name='acp_bearer_token',
                    field=core.db.encrypted_fields.EncryptedCharField(blank=True, db_column='acp_bearer_token_encrypted', default='', help_text="Bearer token the agent platform uses on this tenant's /acp/* checkout surface (served to the agent gateway only via the internal-token-gated tenant resolve endpoint). Per-tenant — a token minted for one store must never drive another store's checkout. Empty disables ACP for this tenant.", max_length=255, verbose_name='ACP Bearer Token'),
                ),
                migrations.AlterField(
                    model_name='tenant',
                    name='acs_api_key',
                    field=core.db.encrypted_fields.EncryptedCharField(blank=True, db_column='acs_api_key_encrypted', default='', help_text='ACS courier API key. No platform fallback — empty disables ACS.', max_length=255, verbose_name='ACS API Key'),
                ),
                migrations.AlterField(
                    model_name='tenant',
                    name='acs_company_password',
                    field=core.db.encrypted_fields.EncryptedCharField(blank=True, db_column='acs_company_password_encrypted', default='', help_text='ACS company password. No platform fallback — empty disables ACS.', max_length=128, verbose_name='ACS Company Password'),
                ),
                migrations.AlterField(
                    model_name='tenant',
                    name='acs_user_password',
                    field=core.db.encrypted_fields.EncryptedCharField(blank=True, db_column='acs_user_password_encrypted', default='', help_text='ACS API user password. No platform fallback — empty disables ACS.', max_length=128, verbose_name='ACS User Password'),
                ),
                migrations.AlterField(
                    model_name='tenant',
                    name='box_now_client_secret',
                    field=core.db.encrypted_fields.EncryptedCharField(blank=True, db_column='box_now_client_secret_encrypted', default='', help_text='BoxNow OAuth2 client secret. No platform fallback — empty disables BoxNow.', max_length=255, verbose_name='BoxNow Client Secret'),
                ),
                migrations.AlterField(
                    model_name='tenant',
                    name='box_now_webhook_secret',
                    field=core.db.encrypted_fields.EncryptedCharField(blank=True, db_column='box_now_webhook_secret_encrypted', default='', help_text='HMAC secret for verifying BoxNow webhook deliveries — per-tenant, since each tenant holds their own BoxNow contract. No platform fallback — empty disables BoxNow.', max_length=255, verbose_name='BoxNow Webhook Secret'),
                ),
                migrations.AlterField(
                    model_name='tenant',
                    name='chat_api_key',
                    field=core.db.encrypted_fields.EncryptedCharField(blank=True, db_column='chat_api_key_encrypted', default='', help_text='LLM API key for the storefront chat assistant (served to the agent gateway only via the internal-token-gated tenant resolve endpoint). Each tenant brings their own key — own quota, consent, and billing. Never expose to the browser. Empty disables the chat assistant for this tenant.', max_length=255, verbose_name='Chat API Key'),
                ),
                migrations.AlterField(
                    model_name='tenant',
                    name='cloudflare_api_token',
                    field=core.db.encrypted_fields.EncryptedCharField(blank=True, db_column='cloudflare_api_token_encrypted', db_default='', default='', help_text="A token with only Zone → Cache Purge → Purge on that zone. Secret — used to clear the store's pages from Cloudflare's cache after an edit or a deploy.", max_length=255, verbose_name='Cloudflare API token'),
                ),
                migrations.AlterField(
                    model_name='tenant',
                    name='meta_capi_access_token',
                    field=core.db.encrypted_fields.EncryptedCharField(blank=True, db_column='meta_capi_access_token_encrypted', default='', help_text='Meta Conversions API system user access token. Secret — never expose to the browser. No platform fallback — empty disables Meta CAPI.', max_length=255, verbose_name='Meta CAPI Access Token'),
                ),
                migrations.AlterField(
                    model_name='tenant',
                    name='stripe_secret_key',
                    field=core.db.encrypted_fields.EncryptedCharField(blank=True, db_column='stripe_secret_key_encrypted', default='', help_text="The tenant's OWN Stripe secret key (sk_live_*, sk_test_* or a restricted rk_* key). Secret — never expose to the browser. Empty means Stripe is unavailable for this tenant — there is no platform-wide fallback.", max_length=255, verbose_name='Stripe secret key'),
                ),
                migrations.AlterField(
                    model_name='tenant',
                    name='viva_wallet_api_key',
                    field=core.db.encrypted_fields.EncryptedCharField(blank=True, db_column='viva_wallet_api_key_encrypted', default='', help_text='Viva Wallet API key (classic auth). No platform fallback — empty disables Viva Wallet.', max_length=255, verbose_name='Viva Wallet API Key'),
                ),
                migrations.AlterField(
                    model_name='tenant',
                    name='viva_wallet_client_secret',
                    field=core.db.encrypted_fields.EncryptedCharField(blank=True, db_column='viva_wallet_client_secret_encrypted', default='', help_text='Viva Wallet OAuth2 client secret. No platform fallback — empty disables Viva Wallet.', max_length=255, verbose_name='Viva Wallet Client Secret'),
                ),
                migrations.AlterField(
                    model_name='tenant',
                    name='viva_wallet_webhook_verification_key',
                    field=core.db.encrypted_fields.EncryptedCharField(blank=True, db_column='viva_wallet_webhook_verification_key_encrypted', default='', help_text='Viva Wallet webhook verification key for HMAC validation. No platform fallback — empty disables Viva Wallet.', max_length=255, verbose_name='Viva Wallet Webhook Verification Key'),
                ),
            ],
        ),
    ]
