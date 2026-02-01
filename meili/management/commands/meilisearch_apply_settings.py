"""
Management command to apply index settings updates without reindexing.

This command updates Meilisearch index settings for ProductTranslation and
BlogPostTranslation indexes based on their MeiliMeta configuration.
"""

from django.core.management.base import BaseCommand
from django.utils.translation import gettext as _

from blog.models.post import BlogPostTranslation
from product.models.product import ProductTranslation


class Command(BaseCommand):
    help = _("Apply index settings updates to Meilisearch without reindexing")

    def add_arguments(self, parser):
        parser.add_argument(
            "--index",
            type=str,
            help=_(
                "Specific index to update (ProductTranslation or BlogPostTranslation)"
            ),
            required=False,
        )

    def handle(self, *args, **options):
        index_name = options.get("index")

        if index_name:
            # Update specific index
            if index_name == "ProductTranslation":
                self._update_product_index()
            elif index_name == "BlogPostTranslation":
                self._update_blog_index()
            else:
                self.stdout.write(
                    self.style.ERROR(
                        f"Unknown index: {index_name}. "
                        "Valid options: ProductTranslation, BlogPostTranslation"
                    )
                )
                return
        else:
            # Update all indexes
            self._update_product_index()
            self._update_blog_index()

        self.stdout.write(
            self.style.SUCCESS("\nAll index settings updated successfully!")
        )

    def _update_product_index(self):
        """Update ProductTranslation index settings."""
        self.stdout.write("\nUpdating ProductTranslation index settings...")

        try:
            ProductTranslation.update_meili_settings()
            self.stdout.write(
                self.style.SUCCESS(
                    "✓ ProductTranslation settings updated successfully"
                )
            )
            self.stdout.write("  - Added searchCutoffMs: 1500ms")
            self.stdout.write(
                "  - Added stock to filterable/sortable/displayed fields"
            )
            self.stdout.write(
                "  - Added active and is_deleted to filterable/displayed fields"
            )
            self.stdout.write(
                "  - Added stock:desc and discount_percent:desc to ranking rules"
            )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(
                    f"✗ Failed to update ProductTranslation settings: {str(e)}"
                )
            )

    def _update_blog_index(self):
        """Update BlogPostTranslation index settings."""
        self.stdout.write("\nUpdating BlogPostTranslation index settings...")

        try:
            BlogPostTranslation.update_meili_settings()
            self.stdout.write(
                self.style.SUCCESS(
                    "✓ BlogPostTranslation settings updated successfully"
                )
            )
            self.stdout.write("  - Added searchCutoffMs: 1500ms")
            self.stdout.write("  - Updated maxTotalHits: 50000 (from 1000)")
            self.stdout.write("  - Updated maxValuesPerFacet: 100 (from 50)")
            self.stdout.write(
                "  - Added view_count and created_at to sortable fields"
            )
            self.stdout.write(
                "  - Added category and is_published to filterable fields"
            )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(
                    f"✗ Failed to update BlogPostTranslation settings: {str(e)}"
                )
            )
