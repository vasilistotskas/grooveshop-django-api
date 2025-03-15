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
    help = "Syncs the MeiliSearch index for the given model."

    def add_arguments(self, parser):
        parser.add_argument(
            "model",
            type=str,
            help="The model to sync the index for. This should be in the format <app_name>.<model_name>",
        )
        parser.add_argument(
            "--batch_size",
            action="store_true",
            default=DEFAULT_BATCH_SIZE,
            help="The batch size you want to import in (default: 1000)",
        )

    def handle(self, *args, **options):
        Model = self._resolve_model(options["model"])
        tasks = []
        for qs in batch_qs(Model.objects.all(), options["batch_size"]):
            tasks.append(
                _client.get_index(
                    Model._meilisearch["index_name"]
                ).add_documents(
                    [self._serialize(m) for m in qs if m.meili_filter()]
                )
            )
        for task in tasks:
            finished = _client.wait_for_task(task.task_uid)
            if finished.status == "failed":
                self.stderr.write(self.style.ERROR(finished.error))
                sys.exit(1)
        self.stdout.write(
            self.style.SUCCESS(f"Synced index for {options['model']}")
        )

    def _serialize(self, model: IndexMixin) -> dict:
        serialized = model.meili_serialize()
        pk = model._meta.pk.value_to_string(model)
        return serialized | {"id": pk, "pk": pk}

    def _resolve_model(self, model: str):
        try:
            Model = apps.get_model(model)
            if IndexMixin not in Model.__mro__:
                raise ValueError("Model does not inherit from IndexMixin")
        except LookupError:
            self.stdout.write(self.style.ERROR(f"Model not found: {model}"))
            sys.exit(1)
        except ValueError:
            self.stdout.write(self.style.ERROR(f"Invalid model: {model}"))
            sys.exit(1)
        return Model
