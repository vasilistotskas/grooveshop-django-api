from django.db import migrations, models


class Migration(migrations.Migration):
    """Release C of the ``is_online_payment`` retirement (expand).

    The code stops reading and writing the two deprecated mirrors of
    ``settlement``. Their columns stay for the release still serving
    during the rollout, which writes them on every save. Each first gets
    a database default, so this release's INSERTs, which no longer name
    them, still succeed; then the fields and their index leave Django's
    state only. The next release drops the columns and the index.
    """

    dependencies = [("pay_way", "0033_remove_german_translations")]

    operations = [
        migrations.AlterField(
            model_name="payway",
            name="is_online_payment",
            field=models.BooleanField(
                default=False,
                db_default=False,
                help_text="Deprecated mirror of ``settlement == ONLINE``. "
                "Dropped in the release after settlement lands.",
                verbose_name="Is Online Payment",
            ),
        ),
        migrations.AlterField(
            model_name="payway",
            name="requires_confirmation",
            field=models.BooleanField(
                default=False,
                db_default=False,
                help_text="Deprecated mirror of ``settlement == "
                "OFFLINE_TRANSFER``. Dropped in the release after "
                "settlement lands.",
                verbose_name="Requires Confirmation",
            ),
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveIndex(
                    model_name="payway", name="pay_way_online_ix"
                ),
                migrations.RemoveField(
                    model_name="payway", name="is_online_payment"
                ),
                migrations.RemoveField(
                    model_name="payway", name="requires_confirmation"
                ),
            ],
            database_operations=[],
        ),
    ]
