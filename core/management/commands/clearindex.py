from django.apps import apps
from django.core.management.base import BaseCommand

from meili._client import client as _client  # noqa
from meili.models import IndexMixin


class Command(BaseCommand):
    help = "Clears the MeiliSearch index for the given model."

    def add_arguments(self, parser):
        parser.add_argument("model", type=str, help="The model to clear the index for.")

    def handle(self, *args, **options):
        model = self._resolve_model(options["model"])
        index = _client.get_index(model._meilisearch["index_name"])  # noqa
        index.delete_all_documents()
        self.stdout.write(self.style.SUCCESS(f"Cleared index for {model}"))

    def _resolve_model(self, model: str) -> type[IndexMixin]:
        try:
            Model = apps.get_model(model)  # noqa
            if IndexMixin not in Model.__mro__:
                raise ValueError("Model does not inherit from IndexMixin")
        except LookupError:
            self.stdout.write(self.style.ERROR(f"Model not found: {model}"))
            exit(1)
        except ValueError:
            self.stdout.write(self.style.ERROR(f"Invalid model: {model}"))
            exit(1)
        return Model
