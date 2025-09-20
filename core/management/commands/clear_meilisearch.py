from django.apps import apps
from django.core.management.base import BaseCommand

from meili._client import client as _client
from meili.models import IndexMixin


class Command(BaseCommand):
    help = "Clears all MeiliSearch indexes and data (equivalent to clearing the MeiliSearch database)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--recreate",
            action="store_true",
            help="Recreate indexes after clearing (triggers model initialization)",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Skip confirmation prompt",
        )

    def handle(self, *args, **options):
        if not options["force"]:
            confirm = input(
                "This will delete ALL MeiliSearch indexes and data. "
                "Are you sure? (yes/no): "
            )
            if confirm.lower() not in ["yes", "y"]:
                self.stdout.write(self.style.WARNING("Operation cancelled"))
                return

        try:
            indexes = _client.get_indexes()

            if not indexes:
                self.stdout.write(
                    self.style.WARNING("No indexes found in MeiliSearch")
                )
                return

            self.stdout.write(
                self.style.WARNING(
                    f"Found {len(indexes)} indexes to delete: "
                    f"{', '.join([idx.uid for idx in indexes])}"
                )
            )

            deleted_indexes = []
            for index in indexes:
                try:
                    task = _client.client.delete_index(index.uid)
                    finished = _client.wait_for_task(task.task_uid)

                    if finished.status == "failed":
                        self.stderr.write(
                            self.style.ERROR(
                                f"Failed to delete index '{index.uid}': {finished.error}"
                            )
                        )
                    else:
                        deleted_indexes.append(index.uid)
                        self.stdout.write(
                            self.style.SUCCESS(f"Deleted index: {index.uid}")
                        )

                except Exception as e:
                    self.stderr.write(
                        self.style.ERROR(
                            f"Error deleting index '{index.uid}': {e}"
                        )
                    )

            if deleted_indexes:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Successfully deleted {len(deleted_indexes)} indexes"
                    )
                )

            if options["recreate"]:
                self.stdout.write("Recreating indexes...")
                self._recreate_indexes()

        except Exception as e:
            self.stderr.write(
                self.style.ERROR(f"Error clearing MeiliSearch: {e}")
            )

    def _recreate_indexes(self):
        """Recreate indexes by triggering model initialization"""
        recreated_count = 0

        for app_config in apps.get_app_configs():
            for model in app_config.get_models():
                if IndexMixin in model.__mro__:
                    try:
                        index_name = model._meilisearch["index_name"]
                        primary_key = model._meilisearch["primary_key"]

                        _client.create_index(index_name, primary_key)

                        self.stdout.write(
                            self.style.SUCCESS(f"Recreated index: {index_name}")
                        )
                        recreated_count += 1

                    except Exception as e:
                        self.stderr.write(
                            self.style.ERROR(
                                f"Error recreating index for {model.__name__}: {e}"
                            )
                        )

        if recreated_count > 0:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully recreated {recreated_count} indexes"
                )
            )
        else:
            self.stdout.write(self.style.WARNING("No indexes were recreated"))
