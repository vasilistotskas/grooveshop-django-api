import sys
from contextlib import nullcontext as _nullcontext
from typing import cast

from django.apps import apps
from django.core.management.base import BaseCommand

from meili._client import client as _client
from meili.management.tenant_mixin import TenantCommandMixin
from meili.models import IndexMixin


class Command(TenantCommandMixin, BaseCommand):
    help = "Clears the MeiliSearch index for the given model."

    def add_arguments(self, parser):
        parser.add_argument(
            "model", type=str, help="The model to clear the index for."
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
        model = self._resolve_model(options["model"])
        index = _client.get_index(model.get_meili_index_name())
        task = index.delete_all_documents()
        finished = _client.wait_for_task(task.task_uid)
        if finished.status == "failed":
            raise Exception(finished)
        self.stdout.write(self.style.SUCCESS(f"Cleared index for {model}"))

    def _resolve_model(self, model: str) -> type[IndexMixin]:
        Model: type[IndexMixin]
        try:
            Model = cast("type[IndexMixin]", apps.get_model(model))
            if IndexMixin not in Model.__mro__:
                raise ValueError("Model does not inherit from IndexMixin")
        except LookupError:
            self.stdout.write(self.style.ERROR(f"Model not found: {model}"))
            sys.exit(1)
        except ValueError:
            self.stdout.write(self.style.ERROR(f"Invalid model: {model}"))
            sys.exit(1)
        return Model
