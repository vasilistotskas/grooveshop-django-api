from django.db import migrations


class Migration(migrations.Migration):
    """Release N+1: drop the image ``title`` columns 0066 left behind.

    ``IF EXISTS`` keeps it idempotent across tenant schemas.
    """

    contract_of = [("product", "0066_image_title_drop_from_state")]

    dependencies = [
        ("product", "0066_image_title_drop_from_state"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                "ALTER TABLE product_productimage_translation "
                "DROP COLUMN IF EXISTS title; "
                "ALTER TABLE product_productcategoryimage_translation "
                "DROP COLUMN IF EXISTS title"
            ),
            reverse_sql=(
                "ALTER TABLE product_productimage_translation "
                "ADD COLUMN title varchar(50) NULL; "
                "ALTER TABLE product_productcategoryimage_translation "
                "ADD COLUMN title varchar(255) NULL"
            ),
        ),
    ]
