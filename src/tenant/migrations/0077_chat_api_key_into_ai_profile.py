import core.db.encrypted_fields
from django.db import migrations

#: What every store with a chat key ran on before the AI profile: the agent
#: gateway's platform-wide Gemini endpoint and model
#: (grooveshop-infrastructure agent-gateway-config.yaml, CHAT_MODEL).
PROVIDER = "gemini"
MODEL = "gemini-3.5-flash-lite"


class Migration(migrations.Migration):
    """Move the chat key into the AI profile and retire ``chat_api_key``
    (expand).

    The key is copied as stored (one Fernet key encrypts every field, so the
    token stays valid) and the old column is blanked, so no copy of the
    secret is left behind in it. The column stays for the release still
    serving during the rollout, which names it on every read and save; it
    first gets a database default, so this release's INSERTs, which no
    longer name it, still succeed. Then the field leaves Django's state only.
    The next release drops the column.
    """

    dependencies = [("tenant", "0076_tenant_ai_profile")]

    operations = [
        migrations.AlterField(
            model_name="tenant",
            name="chat_api_key",
            field=core.db.encrypted_fields.EncryptedCharField(
                blank=True,
                db_column="chat_api_key_encrypted",
                default="",
                db_default="",
                max_length=255,
                verbose_name="Chat API Key",
            ),
        ),
        migrations.RunSQL(
            sql=[
                (
                    "UPDATE tenant_tenant SET ai_provider = %s, ai_model = %s,"
                    " ai_api_key = chat_api_key_encrypted,"
                    " chat_api_key_encrypted = ''"
                    " WHERE chat_api_key_encrypted <> ''",
                    [PROVIDER, MODEL],
                ),
            ],
            reverse_sql=[
                (
                    "UPDATE tenant_tenant"
                    " SET chat_api_key_encrypted = ai_api_key"
                    " WHERE ai_provider = %s",
                    [PROVIDER],
                ),
            ],
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="tenant", name="chat_api_key"),
            ],
            database_operations=[],
        ),
    ]
