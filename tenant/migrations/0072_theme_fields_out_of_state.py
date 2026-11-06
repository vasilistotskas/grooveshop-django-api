from django.db import migrations, models

#: Each retired field with the database default its column keeps until the
#: next release drops it. The values are the fields' own model defaults.
COLUMNS = {
    "primary_color": models.CharField(
        default="neutral", db_default="neutral", max_length=20
    ),
    "neutral_color": models.CharField(
        default="zinc", db_default="zinc", max_length=20
    ),
    "accent_hex": models.CharField(
        default="#003DFF", db_default="#003DFF", max_length=7
    ),
    "success_hex": models.CharField(
        default="#16a34a", db_default="#16a34a", max_length=7
    ),
    "warning_hex": models.CharField(
        default="#ca8a04", db_default="#ca8a04", max_length=7
    ),
    "error_hex": models.CharField(
        default="#dc2626", db_default="#dc2626", max_length=7
    ),
    "info_hex": models.CharField(
        default="#2563eb", db_default="#2563eb", max_length=7
    ),
    "theme_preset": models.CharField(
        default="default", db_default="default", max_length=20
    ),
    "theme_metadata": models.JSONField(default=dict, db_default={}),
}

MODELS = ("tenant", "historicaltenant")


class Migration(migrations.Migration):
    """Retire the Tenant theme fields (expand).

    A store's look lives in its published theme document, and nothing reads
    these any more. The columns stay for the release still serving during
    the rollout, which names them on every read and save; each first gets a
    database default, so this release's INSERTs, which no longer name them,
    still succeed. Then the fields leave Django's state only. The next
    release drops the columns.
    """

    dependencies = [("tenant", "0071_clarity_project_id")]

    operations = [
        *(
            migrations.AlterField(
                model_name=model, name=name, field=field.clone()
            )
            for model in MODELS
            for name, field in COLUMNS.items()
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name=model, name=name)
                for model in MODELS
                for name in COLUMNS
            ],
            database_operations=[],
        ),
    ]
