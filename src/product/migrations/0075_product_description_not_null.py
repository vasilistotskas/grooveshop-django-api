"""A product description is NOT NULL (release 2 of 2).

0074 made "" the field's empty value in Django's state, backfilled every
NULL and left the column nullable for the release still serving, which
could write NULL until its last pod exited. That release now runs
everywhere and writes "", so this backfills the rows it wrote in that
window and adds the constraint the state already declares.

``RunSQL`` because the field's state is already NOT NULL (0074). The
reverse drops the constraint, the shape 0074 left.
"""

from django.db import migrations


class Migration(migrations.Migration):
    contract_of = [("product", "0074_product_description_empty_string")]

    dependencies = [("product", "0074_product_description_empty_string")]

    operations = [
        migrations.RunSQL(
            sql=(
                "UPDATE product_product_translation "
                "SET description = '' WHERE description IS NULL; "
                "ALTER TABLE product_product_translation "
                "ALTER COLUMN description SET NOT NULL"
            ),
            reverse_sql=(
                "ALTER TABLE product_product_translation "
                "ALTER COLUMN description DROP NOT NULL"
            ),
        ),
    ]
