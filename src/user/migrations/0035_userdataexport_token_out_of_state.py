from django.db import migrations, models


class Migration(migrations.Migration):
    """Expand: nothing writes the export token any more. The column becomes
    nullable, so a release that still writes it and this one can run side by
    side, and leaves the state; 0036 drops it."""

    dependencies = [
        ("user", "0034_usersubscription_already_subscribed_notice"),
    ]

    operations = [
        migrations.AlterField(
            model_name="userdataexport",
            name="token",
            field=models.CharField(
                db_index=True,
                help_text="Random token carried by the download URL.",
                max_length=64,
                null=True,
                unique=True,
                verbose_name="Download Token",
            ),
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(
                    model_name="userdataexport",
                    name="token",
                ),
            ],
            database_operations=[],
        ),
        migrations.AlterField(
            model_name="userdataexport",
            name="expires_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When the export can no longer be downloaded.",
                null=True,
                verbose_name="Expires At",
            ),
        ),
    ]
