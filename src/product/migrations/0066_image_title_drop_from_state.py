from django.db import migrations


class Migration(migrations.Migration):
    """Release N of retiring the image ``title``: state only.

    The columns stay (both are nullable) so the release still serving
    keeps reading and writing them; 0067 drops them once this release
    runs in production.
    """

    dependencies = [
        ("product", "0065_product_highlight"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(
                    model_name="productimagetranslation",
                    name="title",
                ),
                migrations.RemoveField(
                    model_name="productcategoryimagetranslation",
                    name="title",
                ),
            ],
            database_operations=[],
        ),
    ]
