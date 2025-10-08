"""Management command to update Meilisearch index settings."""

from django.core.management.base import BaseCommand
from django.utils.translation import gettext_lazy as _

from blog.models.post import BlogPostTranslation
from meili._client import client
from meili.dataclasses import MeiliIndexSettings
from product.models.product import ProductTranslation


class Command(BaseCommand):
    """Update Meilisearch index settings (synonyms, filters, etc.) without reindexing."""

    help = _(
        "Update Meilisearch index settings (synonyms, filters, etc.) without reindexing"
    )

    def add_arguments(self, parser):
        """Add command arguments."""
        parser.add_argument(
            "--index",
            type=str,
            help=_("Specific index to update (product or blog)"),
            choices=["product", "blog"],
        )

    def handle(self, *args, **options):
        """Execute the command."""
        index_name = options.get("index")

        if index_name == "product" or not index_name:
            self.update_product_settings()

        if index_name == "blog" or not index_name:
            self.update_blog_settings()

        self.stdout.write(
            self.style.SUCCESS(
                _("✅ Meilisearch settings updated successfully!")
            )
        )

    def update_product_settings(self):
        """Update product index settings."""
        self.stdout.write(_("📦 Updating product index settings..."))

        index_name = ProductTranslation._meilisearch["index_name"]
        meili_meta = ProductTranslation.MeiliMeta

        settings = MeiliIndexSettings(
            displayed_fields=getattr(meili_meta, "displayed_fields", None),
            searchable_fields=getattr(meili_meta, "searchable_fields", None),
            filterable_fields=getattr(meili_meta, "filterable_fields", None),
            sortable_fields=getattr(meili_meta, "sortable_fields", None),
            ranking_rules=getattr(meili_meta, "ranking_rules", None),
            stop_words=getattr(meili_meta, "stop_words", None),
            synonyms=getattr(meili_meta, "synonyms", None),
            distinct_attribute=getattr(meili_meta, "distinct_attribute", None),
            typo_tolerance=getattr(meili_meta, "typo_tolerance", None),
            faceting=getattr(meili_meta, "faceting", None),
            pagination=getattr(meili_meta, "pagination", None),
        )

        client.with_settings(index_name, settings)

        self.stdout.write(
            self.style.SUCCESS(
                _("✅ Product settings updated for index: {}").format(
                    index_name
                )
            )
        )

        if settings.synonyms:
            self.stdout.write(
                _("  📝 Synonyms: {} entries").format(len(settings.synonyms))
            )

    def update_blog_settings(self):
        """Update blog post index settings."""
        self.stdout.write(_("📝 Updating blog post index settings..."))

        index_name = BlogPostTranslation._meilisearch["index_name"]
        meili_meta = BlogPostTranslation.MeiliMeta

        settings = MeiliIndexSettings(
            displayed_fields=getattr(meili_meta, "displayed_fields", None),
            searchable_fields=getattr(meili_meta, "searchable_fields", None),
            filterable_fields=getattr(meili_meta, "filterable_fields", None),
            sortable_fields=getattr(meili_meta, "sortable_fields", None),
            ranking_rules=getattr(meili_meta, "ranking_rules", None),
            stop_words=getattr(meili_meta, "stop_words", None),
            synonyms=getattr(meili_meta, "synonyms", None),
            distinct_attribute=getattr(meili_meta, "distinct_attribute", None),
            typo_tolerance=getattr(meili_meta, "typo_tolerance", None),
            faceting=getattr(meili_meta, "faceting", None),
            pagination=getattr(meili_meta, "pagination", None),
        )

        client.with_settings(index_name, settings)

        self.stdout.write(
            self.style.SUCCESS(
                _("✅ Blog settings updated for index: {}").format(index_name)
            )
        )

        if settings.synonyms:
            self.stdout.write(
                _("  📝 Synonyms: {} entries").format(len(settings.synonyms))
            )
