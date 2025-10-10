import sys

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand

from meili._client import client as _client
from meili.models import IndexMixin

DEFAULT_BATCH_SIZE = settings.MEILISEARCH.get("DEFAULT_BATCH_SIZE", 1000)


def batch_qs(qs, batch_size=DEFAULT_BATCH_SIZE):
    total = qs.count()
    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        yield qs[start:end]


class Command(BaseCommand):
    help = (
        "Syncs all MeiliSearch indexes for models that inherit from IndexMixin."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch_size",
            type=int,
            default=DEFAULT_BATCH_SIZE,
            help="The batch size you want to import in (default: 1000)",
        )
        parser.add_argument(
            "--app",
            type=str,
            default=None,
            help="Only sync models from the specified app",
        )
        parser.add_argument(
            "--exclude",
            type=str,
            nargs="+",
            default=[],
            help="Exclude specific models (format: app_label.ModelName)",
        )

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        app_filter = options["app"]
        exclude_models = set(options["exclude"])

        indexable_models = self._discover_indexable_models(
            app_filter, exclude_models
        )

        if not indexable_models:
            self.stdout.write(self.style.WARNING("No indexable models found."))
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Found {len(indexable_models)} indexable model(s) to sync:"
            )
        )
        for model in indexable_models:
            self.stdout.write(f"  - {model._meta.label}")

        self.stdout.write("")

        total_synced = 0
        failed_models = []

        for Model in indexable_models:
            model_label = Model._meta.label
            self.stdout.write(f"Syncing {model_label}...")

            try:
                self._update_settings(Model)
                count = self._sync_model(Model, batch_size)
                total_synced += count
                self.stdout.write(
                    self.style.SUCCESS(f"  OK Synced {count} document(s)")
                )
            except Exception as e:
                failed_models.append((model_label, str(e)))
                self.stdout.write(self.style.ERROR(f"  ERROR Failed: {e}"))

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=" * 50))
        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully synced {len(indexable_models) - len(failed_models)}/{len(indexable_models)} model(s)"
            )
        )
        self.stdout.write(
            self.style.SUCCESS(f"Total documents synced: {total_synced}")
        )

        if failed_models:
            self.stdout.write("")
            self.stdout.write(self.style.ERROR("Failed models:"))
            for model_label, error in failed_models:
                self.stdout.write(
                    self.style.ERROR(f"  - {model_label}: {error}")
                )
            sys.exit(1)

    def _discover_indexable_models(self, app_filter=None, exclude_models=None):
        """Discover all models that inherit from IndexMixin."""
        indexable_models = []
        exclude_models = exclude_models or set()

        for model in apps.get_models():
            if app_filter and model._meta.app_label != app_filter:
                continue

            if model._meta.label in exclude_models:
                continue

            if IndexMixin in model.__mro__:
                indexable_models.append(model)

        return indexable_models

    def _sync_model(self, Model, batch_size):
        """Sync a single model's index."""
        tasks = []
        total_count = 0

        for qs in batch_qs(Model.objects.all(), batch_size):
            documents = [self._serialize(m) for m in qs if m.meili_filter()]
            total_count += len(documents)

            if documents:
                task = _client.get_index(
                    Model._meilisearch["index_name"]
                ).add_documents(documents)
                tasks.append(task)

        for task in tasks:
            finished = _client.wait_for_task(task.task_uid)
            if finished.status == "failed":
                raise Exception(finished.error)

        return total_count

    def _update_settings(self, Model):
        """Update settings for a single model's index using the model's method."""
        Model.update_meili_settings()

    def _serialize(self, model: IndexMixin) -> dict:
        """Serialize a model instance for MeiliSearch."""
        serialized = model.meili_serialize()
        pk = model._meta.pk.value_to_string(model)
        return serialized | {"id": pk, "pk": pk}
