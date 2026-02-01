"""
Management command to update Meilisearch index settings.

This command allows updating specific index settings like maxTotalHits,
searchCutoffMs, and maxValuesPerFacet without requiring full reindexing.
"""

from django.core.management.base import BaseCommand
from django.utils.translation import gettext as _

from blog.models.post import BlogPostTranslation
from meili._client import client as meili_client
from meili.dataclasses import MeiliIndexSettings
from product.models.product import ProductTranslation


class Command(BaseCommand):
    help = _("Update Meilisearch index settings without reindexing")

    AVAILABLE_INDEXES = {
        "ProductTranslation": ProductTranslation,
        "BlogPostTranslation": BlogPostTranslation,
    }

    def add_arguments(self, parser):
        parser.add_argument(
            "--index",
            type=str,
            help=_(
                "Index to update. Available: "
                + ", ".join(self.AVAILABLE_INDEXES.keys())
            ),
            required=True,
        )
        parser.add_argument(
            "--max-total-hits",
            type=int,
            help=_("Maximum total hits for pagination (maxTotalHits)"),
            required=False,
        )
        parser.add_argument(
            "--search-cutoff-ms",
            type=int,
            help=_("Search timeout in milliseconds (searchCutoffMs)"),
            required=False,
        )
        parser.add_argument(
            "--max-values-per-facet",
            type=int,
            help=_("Maximum values per facet (maxValuesPerFacet)"),
            required=False,
        )

    def handle(self, *args, **options):
        index_name = options["index"]
        max_total_hits = options.get("max_total_hits")
        search_cutoff_ms = options.get("search_cutoff_ms")
        max_values_per_facet = options.get("max_values_per_facet")

        # Validate index name
        if index_name not in self.AVAILABLE_INDEXES:
            self.stdout.write(
                self.style.ERROR(
                    f"Unknown index: {index_name}\n\nAvailable indexes:\n"
                )
            )
            for idx_name in self.AVAILABLE_INDEXES.keys():
                self.stdout.write(f"  - {idx_name}")
            return

        # Check if at least one setting is provided
        if not any([max_total_hits, search_cutoff_ms, max_values_per_facet]):
            self.stdout.write(
                self.style.ERROR(
                    "At least one setting must be provided:\n"
                    "  --max-total-hits\n"
                    "  --search-cutoff-ms\n"
                    "  --max-values-per-facet"
                )
            )
            return

        # Get model class
        model_class = self.AVAILABLE_INDEXES[index_name]

        # Display action
        self.stdout.write(f"\nUpdating {index_name} settings...")

        # Build settings update
        settings_kwargs = {}

        if max_total_hits is not None:
            settings_kwargs["pagination"] = {"maxTotalHits": max_total_hits}
            self.stdout.write(f"  - maxTotalHits: {max_total_hits}")

        if search_cutoff_ms is not None:
            settings_kwargs["search_cutoff_ms"] = search_cutoff_ms
            self.stdout.write(f"  - searchCutoffMs: {search_cutoff_ms}ms")

        if max_values_per_facet is not None:
            settings_kwargs["faceting"] = {
                "maxValuesPerFacet": max_values_per_facet
            }
            self.stdout.write(f"  - maxValuesPerFacet: {max_values_per_facet}")

        try:
            # Create settings object
            index_settings = MeiliIndexSettings(**settings_kwargs)

            # Get index name from model
            meili_index_name = model_class._meilisearch["index_name"]

            # Apply settings
            meili_client.with_settings(meili_index_name, index_settings)

            self.stdout.write(
                self.style.SUCCESS(
                    f"\n✓ Successfully updated {index_name} settings"
                )
            )
            self.stdout.write(
                "\nNote: Settings are applied immediately without reindexing documents."
            )

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(
                    f"✗ Failed to update {index_name} settings: {str(e)}"
                )
            )
