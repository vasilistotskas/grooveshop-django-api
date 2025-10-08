"""Management command to reindex Meilisearch indices."""

from django.core.management.base import BaseCommand
from django.utils.translation import gettext_lazy as _

from blog.models.post import BlogPostTranslation
from meili._client import client
from product.models.product import ProductTranslation


class Command(BaseCommand):
    """Reindex all Meilisearch indices with updated settings and synonyms."""

    help = _("Reindex Meilisearch indices with updated settings and synonyms")

    def add_arguments(self, parser):
        """Add command arguments."""
        parser.add_argument(
            "--index",
            type=str,
            help=_("Specific index to reindex (product or blog)"),
            choices=["product", "blog"],
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help=_("Clear the index before reindexing"),
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help=_("Batch size for indexing documents (default: 1000)"),
        )

    def handle(self, *args, **options):
        """Execute the command."""
        index_name = options.get("index")
        clear = options.get("clear")
        batch_size = options.get("batch_size")

        if index_name == "product" or not index_name:
            self.reindex_products(clear, batch_size)

        if index_name == "blog" or not index_name:
            self.reindex_blog_posts(clear, batch_size)

        self.stdout.write(
            self.style.SUCCESS(_("✅ Reindexing completed successfully!"))
        )

    def reindex_products(self, clear: bool, batch_size: int):
        """Reindex product translations."""
        self.stdout.write(_("📦 Reindexing products..."))

        index_name = ProductTranslation._meilisearch["index_name"]
        index = client.get_index(index_name)

        if clear:
            self.stdout.write(
                self.style.WARNING(
                    _("⚠️  Clearing product index: {}").format(index_name)
                )
            )
            index.delete_all_documents()

        products = ProductTranslation.objects.select_related("master").all()
        total = products.count()

        self.stdout.write(
            _("Found {} product translations to index").format(total)
        )

        for i in range(0, total, batch_size):
            batch = products[i : i + batch_size]
            documents = [p.meili_serialize() for p in batch if p.meili_filter()]

            if documents:
                task = index.add_documents(documents)
                client.wait_for_task(task.task_uid)

                self.stdout.write(
                    _("Indexed {}/{} products").format(
                        min(i + batch_size, total), total
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(_("✅ Products reindexed successfully!"))
        )

    def reindex_blog_posts(self, clear: bool, batch_size: int):
        """Reindex blog post translations."""
        self.stdout.write(_("📝 Reindexing blog posts..."))

        index_name = BlogPostTranslation._meilisearch["index_name"]
        index = client.get_index(index_name)

        if clear:
            self.stdout.write(
                self.style.WARNING(
                    _("⚠️  Clearing blog index: {}").format(index_name)
                )
            )
            index.delete_all_documents()

        posts = BlogPostTranslation.objects.select_related("master").all()
        total = posts.count()

        self.stdout.write(
            _("Found {} blog post translations to index").format(total)
        )

        for i in range(0, total, batch_size):
            batch = posts[i : i + batch_size]
            documents = [p.meili_serialize() for p in batch if p.meili_filter()]

            if documents:
                task = index.add_documents(documents)
                client.wait_for_task(task.task_uid)

                self.stdout.write(
                    _("Indexed {}/{} blog posts").format(
                        min(i + batch_size, total), total
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(_("✅ Blog posts reindexed successfully!"))
        )
