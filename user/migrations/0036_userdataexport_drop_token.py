from django.db import migrations


class Migration(migrations.Migration):
    """Contract: drop the export token column 0035 took out of the state."""

    contract_of = [("user", "0035_userdataexport_token_out_of_state")]

    dependencies = [
        ("user", "0035_userdataexport_token_out_of_state"),
    ]

    operations = [
        migrations.RunSQL(
            "ALTER TABLE user_userdataexport DROP COLUMN IF EXISTS token",
            reverse_sql=(
                "ALTER TABLE user_userdataexport "
                "ADD COLUMN IF NOT EXISTS token varchar(64) NULL UNIQUE"
            ),
        ),
    ]
