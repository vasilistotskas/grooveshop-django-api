from django.db import migrations, models


class Migration(migrations.Migration):
    """Retire ``Tenant.meta_capi_dataset_id`` (expand).

    Nothing ever read it: the Conversions API posts to the store's
    ``meta_pixel_id``, which is the dataset, so the field only told a
    merchant that leaving it empty disabled Meta CAPI, which it did not.
    The code stops reading and writing it. The columns stay for the
    release still serving during the rollout, which writes them on every
    save; each first gets a database default, so this release's INSERTs,
    which no longer name them, still succeed. Then the fields leave
    Django's state only. The next release drops the columns.
    """

    dependencies = [("tenant", "0060_blank_non_profile_socials")]

    operations = [
        migrations.AlterField(
            model_name="tenant",
            name="meta_capi_dataset_id",
            field=models.CharField(
                blank=True,
                default="",
                db_default="",
                max_length=64,
                verbose_name="Meta CAPI Dataset ID",
            ),
        ),
        migrations.AlterField(
            model_name="historicaltenant",
            name="meta_capi_dataset_id",
            field=models.CharField(
                blank=True,
                default="",
                db_default="",
                max_length=64,
                verbose_name="Meta CAPI Dataset ID",
            ),
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(
                    model_name="tenant", name="meta_capi_dataset_id"
                ),
                migrations.RemoveField(
                    model_name="historicaltenant",
                    name="meta_capi_dataset_id",
                ),
            ],
            database_operations=[],
        ),
    ]
