"""A product description with no text is "", never NULL (release 1 of 2).

The column allowed NULL, so "no description" had two spellings, and the
search hit, which declares ``description`` a string, served NULL as
``null``: the storefront answers 422 to such a hit (#684).

Release 1 (expand): the field leaves NULL in Django's state only, so
every writer the code has (the ORM default, the API's translation
serializers, the admin) writes "", and every NULL already stored becomes
"". The column stays nullable in the database: the release still
serving may write NULL until its last pod exits, and the backfill below
leaves it to the next release to catch those rows.

Release 2 (contract, once this one runs in production): backfill again
and ``ALTER COLUMN description SET NOT NULL`` in a ``RunSQL`` that
declares ``contract_of = [("product", "0074_product_description_empty_string")]``,
then reindex the products in Meilisearch. That release also removes
``test_the_migration_turns_every_null_into_empty``, which seeds a NULL
the constraint then refuses.
"""

import core.fields.rich_text
from django.db import migrations


def null_to_empty(apps, schema_editor):
    ProductTranslation = apps.get_model("product", "ProductTranslation")
    ProductTranslation.objects.filter(description__isnull=True).update(
        description=""
    )


class Migration(migrations.Migration):

    dependencies = [
        ("product", "0073_review_comment_max_length"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="producttranslation",
                    name="description",
                    field=core.fields.rich_text.RichTextField(
                        blank=True, default="", verbose_name="Description"
                    ),
                ),
            ],
            database_operations=[],
        ),
        migrations.RunPython(null_to_empty, migrations.RunPython.noop),
    ]
