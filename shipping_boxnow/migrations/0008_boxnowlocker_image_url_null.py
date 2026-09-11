from django.db import migrations, models


def blank_image_url_to_null(apps, schema_editor):
    """Every locker BoxNow has ever sent us carries no image, and the
    sync stored that as "" — which is neither a URL nor null, so it
    failed the nullable-URL contract the API publishes. The order
    response embeds the locker, so a single blank row 422'd the whole
    payload for a customer whose order had already been created.
    """
    apps.get_model("shipping_boxnow", "BoxNowLocker").objects.filter(
        image_url=""
    ).update(image_url=None)


def null_image_url_to_blank(apps, schema_editor):
    apps.get_model("shipping_boxnow", "BoxNowLocker").objects.filter(
        image_url=None
    ).update(image_url="")


class Migration(migrations.Migration):
    dependencies = [
        ("shipping_boxnow", "0007_alter_boxnowlocker_image_url_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="boxnowlocker",
            name="image_url",
            field=models.URLField(
                blank=True,
                default=None,
                max_length=500,
                null=True,
                verbose_name="Image URL",
            ),
        ),
        migrations.RunPython(
            blank_image_url_to_null,
            null_image_url_to_blank,
        ),
    ]
