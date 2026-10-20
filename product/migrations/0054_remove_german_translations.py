from django.db import migrations

from core.utils.i18n import delete_translations_in


class Migration(migrations.Migration):
    """German is no longer a language of the platform: drop its rows."""

    dependencies = [("product", "0053_review_status_labels")]

    operations = [
        migrations.RunPython(
            delete_translations_in(
                "de",
                "product.AttributeTranslation",
                "product.AttributeValueTranslation",
                "product.ProductCategoryTranslation",
                "product.ProductCategoryImageTranslation",
                "product.ProductImageTranslation",
                "product.ProductTranslation",
                "product.ProductReviewTranslation",
                "product.ProductVariantGroupTranslation",
            ),
            migrations.RunPython.noop,
        ),
    ]
