"""Management command to inspect Meilisearch index details."""

import json
from contextlib import nullcontext as _nullcontext

from django.core.management.base import BaseCommand
from django.utils.translation import gettext_lazy as _
from meilisearch.errors import MeilisearchApiError

from blog.models.post import BlogPostTranslation
from meili._client import client
from meili.management.tenant_mixin import TenantCommandMixin
from product.models.product import ProductTranslation


class Command(TenantCommandMixin, BaseCommand):
    """Inspect Meilisearch index settings, statistics, and configuration."""

    help = _(
        "Inspect Meilisearch index settings, statistics, and configuration"
    )

    def add_arguments(self, parser):
        """Add command arguments."""
        parser.add_argument(
            "--index",
            type=str,
            help=_("Specific index to inspect (product or blog)"),
            choices=["product", "blog"],
        )
        parser.add_argument(
            "--show-synonyms",
            action="store_true",
            help=_("Show all configured synonyms"),
        )
        parser.add_argument(
            "--show-settings",
            action="store_true",
            help=_("Show detailed index settings"),
        )
        self.add_tenant_arguments(parser)

    def handle(self, *args, **options):
        from django_tenants.utils import schema_context

        for schema in self.get_tenant_schemas(options):
            if schema:
                self.stdout.write(
                    self.style.MIGRATE_HEADING(f"\n>>> Tenant: {schema}")
                )
            with schema_context(schema) if schema else _nullcontext():
                self._handle_for_schema(*args, **options)

    def _handle_for_schema(self, *args, **options):
        """Execute the command."""
        index_name = options.get("index")
        show_synonyms: bool = options.get("show_synonyms", False)
        show_settings: bool = options.get("show_settings", False)

        if index_name == "product" or not index_name:
            self.inspect_product_index(show_synonyms, show_settings)

        if index_name == "blog" or not index_name:
            self.inspect_blog_index(show_synonyms, show_settings)

    def inspect_product_index(self, show_synonyms: bool, show_settings: bool):
        """Inspect product index."""
        self.stdout.write(self.style.HTTP_INFO("\n" + "=" * 60))
        self.stdout.write(
            self.style.HTTP_INFO(str(_("PRODUCT INDEX INSPECTION")))
        )
        self.stdout.write(self.style.HTTP_INFO("=" * 60))

        index_name = ProductTranslation.get_meili_index_name()

        try:
            index = client.get_index(index_name)
            stats = index.get_stats()
            self.stdout.write(f"\n{_('Index Name')}: {index_name}")
            self.stdout.write(
                f"{_('Documents')}: {stats.number_of_documents:,}"
            )
            self.stdout.write(f"{_('Indexing')}: {stats.is_indexing}")

            settings = index.get_settings()

            self.stdout.write(f"\n{_('Search Configuration')}:")
            self.stdout.write(
                f"  {_('Searchable Fields')}: {', '.join(settings['searchableAttributes'])}"
            )
            self.stdout.write(
                f"  {_('Filterable Fields')}: {', '.join(settings['filterableAttributes'])}"
            )
            self.stdout.write(
                f"  {_('Sortable Fields')}: {', '.join(settings['sortableAttributes'])}"
            )

            if show_synonyms and settings.get("synonyms"):
                self.stdout.write(
                    f"\n{_('Synonyms')} ({len(settings['synonyms'])} entries):"
                )
                for key, values in settings["synonyms"].items():
                    self.stdout.write(f"  {key} -> {', '.join(values)}")

            if show_settings:
                self.stdout.write(f"\n{_('Detailed Settings')}:")
                self.stdout.write(
                    json.dumps(settings, indent=2, ensure_ascii=False)
                )
        except MeilisearchApiError as e:
            if "index_not_found" in str(e):
                self.stdout.write(
                    self.style.WARNING(
                        f"\n{_('Index does not exist')}: {index_name}"
                    )
                )
                self.stdout.write(
                    self.style.WARNING(
                        f"{_('Run')} 'python manage.py sync_index_to_meilisearch' {_('to create and populate the index.')}"
                    )
                )
            else:
                raise

    def inspect_blog_index(self, show_synonyms: bool, show_settings: bool):
        """Inspect blog post index."""
        self.stdout.write(self.style.HTTP_INFO("\n" + "=" * 60))
        self.stdout.write(
            self.style.HTTP_INFO(str(_("BLOG POST INDEX INSPECTION")))
        )
        self.stdout.write(self.style.HTTP_INFO("=" * 60))

        index_name = BlogPostTranslation.get_meili_index_name()

        try:
            index = client.get_index(index_name)
            stats = index.get_stats()
            self.stdout.write(f"\n{_('Index Name')}: {index_name}")
            self.stdout.write(
                f"{_('Documents')}: {stats.number_of_documents:,}"
            )
            self.stdout.write(f"{_('Indexing')}: {stats.is_indexing}")

            settings = index.get_settings()

            self.stdout.write(f"\n{_('Search Configuration')}:")
            self.stdout.write(
                f"  {_('Searchable Fields')}: {', '.join(settings['searchableAttributes'])}"
            )
            self.stdout.write(
                f"  {_('Filterable Fields')}: {', '.join(settings['filterableAttributes'])}"
            )
            self.stdout.write(
                f"  {_('Sortable Fields')}: {', '.join(settings['sortableAttributes'])}"
            )

            if show_synonyms and settings.get("synonyms"):
                self.stdout.write(
                    f"\n{_('Synonyms')} ({len(settings['synonyms'])} entries):"
                )
                for key, values in settings["synonyms"].items():
                    self.stdout.write(f"  {key} -> {', '.join(values)}")

            if show_settings:
                self.stdout.write(f"\n{_('Detailed Settings')}:")
                self.stdout.write(
                    json.dumps(settings, indent=2, ensure_ascii=False)
                )
        except MeilisearchApiError as e:
            if "index_not_found" in str(e):
                self.stdout.write(
                    self.style.WARNING(
                        f"\n{_('Index does not exist')}: {index_name}"
                    )
                )
                self.stdout.write(
                    self.style.WARNING(
                        f"{_('Run')} 'python manage.py sync_index_to_meilisearch' {_('to create and populate the index.')}"
                    )
                )
            else:
                raise

        self.stdout.write("\n")
