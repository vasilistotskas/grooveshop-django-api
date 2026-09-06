"""
Management command to apply index settings updates without reindexing.

This command updates Meilisearch index settings for ProductTranslation and
BlogPostTranslation indexes based on their MeiliMeta configuration.

CROSS-REPO CONSUMER - do not delete: the grooveshop-infrastructure
prepare-helm PreSync job runs this on EVERY deploy
(manifests/app-constructs/grooveshop/prepare-helm/templates/job.yaml)
so MeiliMeta settings changes (sortable/filterable/ranking fields) never
drift from the live indexes. A drifted sortable field once made every
``?sort=`` product query 500.
"""

from contextlib import nullcontext as _nullcontext

from django.core.management.base import BaseCommand, CommandError
from django.utils.translation import gettext as _

from blog.models.post import BlogPostTranslation
from core.management.tenant_mixin import TenantCommandMixin
from product.models.product import ProductTranslation


class Command(TenantCommandMixin, BaseCommand):
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
        self.add_tenant_arguments(parser)

    def handle(self, *args, **options):
        self._failures: list[str] = []
        from django_tenants.utils import schema_context

        for schema in self.get_tenant_schemas(options):
            if schema:
                self.stdout.write(
                    self.style.MIGRATE_HEADING(f"\n>>> Tenant: {schema}")
                )
            with schema_context(schema) if schema else _nullcontext():
                self._handle_for_schema(*args, **options)

        # Raised after EVERY schema has been attempted, never inside the
        # loop. This is the PreSync hook on every deploy: aborting on the
        # first failing tenant would leave every store after it on the
        # settings drift the command exists to prevent, the drift that
        # "once made every ?sort= product query 500". Exiting non-zero is
        # still non-negotiable; announcing success after catching a
        # failure is what this replaced.
        if self._failures:
            raise CommandError(
                "Index settings NOT applied: " + "; ".join(self._failures)
            )

        self.stdout.write(
            self.style.SUCCESS("\nAll index settings updated successfully!")
        )

    def _handle_for_schema(self, *args, **options):
        index_name = options.get("index")

        if index_name:
            # Update specific index
            if index_name == "ProductTranslation":
                self._update_product_index()
            elif index_name == "BlogPostTranslation":
                self._update_blog_index()
            else:
                # A CommandError, not an error line and exit 0: a typo in
                # --index otherwise reported "unknown" and then still
                # succeeded, having applied nothing.
                raise CommandError(
                    f"Unknown index: {index_name}. "
                    "Valid options: ProductTranslation, BlogPostTranslation"
                )
        else:
            # Update all indexes
            self._update_product_index()
            self._update_blog_index()

    def _record_failure(self, index_name: str, exc: Exception) -> None:
        """Record a failure against the schema it happened in.

        The run continues to the remaining tenants, so an entry naming
        only the index would not say WHICH store is still drifted.
        ``connection.schema_name`` is read here rather than threaded
        through because every caller runs inside ``schema_context``.
        """
        from django.db import connection

        self._failures.append(f"{connection.schema_name}/{index_name}: {exc!s}")

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
                    f"✗ Failed to update ProductTranslation settings: {e!s}"
                )
            )
            self._record_failure("ProductTranslation", e)

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
                    f"✗ Failed to update BlogPostTranslation settings: {e!s}"
                )
            )
            self._record_failure("BlogPostTranslation", e)
