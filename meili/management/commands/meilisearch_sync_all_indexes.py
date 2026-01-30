import time

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from meili._client import client as _client
from meili.models import IndexMixin

DEFAULT_BATCH_SIZE = settings.MEILISEARCH.get("DEFAULT_BATCH_SIZE", 1000)


def batch_qs(qs, batch_size=DEFAULT_BATCH_SIZE):
    total = qs.count()
    if total == 0:
        return
    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        batch = qs[start:end]
        if batch:
            yield batch


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
        start_time = time.perf_counter()
        close_old_connections()

        batch_size = options["batch_size"]
        app_filter = options["app"]
        exclude_models = set(options["exclude"])

        self.stdout.write(self.style.MIGRATE_HEADING("=" * 70))
        self.stdout.write(
            self.style.MIGRATE_HEADING("  MeiliSearch Index Synchronization")
        )
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 70))
        self.stdout.write("")

        # Discovery phase
        discovery_start = time.perf_counter()
        indexable_models = self._discover_indexable_models(
            app_filter, exclude_models
        )
        discovery_time = time.perf_counter() - discovery_start

        if not indexable_models:
            self.stdout.write(self.style.WARNING("No indexable models found."))
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"✓ Discovered {len(indexable_models)} indexable model(s) in {discovery_time:.2f}s"
            )
        )
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_LABEL("Models to sync:"))
        for model in indexable_models:
            self.stdout.write(f"  • {model._meta.label}")

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("-" * 70))
        self.stdout.write("")

        total_synced = 0
        failed_models = []
        model_timings = []

        for idx, Model in enumerate(indexable_models, 1):
            model_label = Model._meta.label
            model_start = time.perf_counter()

            self.stdout.write(
                self.style.MIGRATE_LABEL(
                    f"[{idx}/{len(indexable_models)}] Syncing {model_label}..."
                )
            )

            try:
                close_old_connections()

                # Update settings phase
                settings_start = time.perf_counter()
                self._update_settings(Model)
                settings_time = time.perf_counter() - settings_start
                self.stdout.write(
                    f"  ├─ Settings updated in {settings_time:.2f}s"
                )

                # Sync phase
                sync_start = time.perf_counter()
                count = self._sync_model(Model, batch_size)
                sync_time = time.perf_counter() - sync_start

                model_elapsed = time.perf_counter() - model_start
                model_timings.append((model_label, model_elapsed, count))

                total_synced += count
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  └─ ✓ Synced {count:,} document(s) in {sync_time:.2f}s "
                        f"(total: {model_elapsed:.2f}s, ~{count / sync_time if sync_time > 0 else 0:.0f} docs/s)"
                    )
                )
            except Exception as e:
                model_elapsed = time.perf_counter() - model_start
                failed_models.append((model_label, str(e), model_elapsed))
                self.stdout.write(
                    self.style.ERROR(
                        f"  └─ ✗ Failed after {model_elapsed:.2f}s: {e}"
                    )
                )
                close_old_connections()

            self.stdout.write("")

        total_time = time.perf_counter() - start_time

        # Summary section
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 70))
        self.stdout.write(
            self.style.MIGRATE_HEADING("  Synchronization Summary")
        )
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 70))
        self.stdout.write("")

        success_count = len(indexable_models) - len(failed_models)
        self.stdout.write(
            self.style.SUCCESS(
                f"✓ Successfully synced: {success_count}/{len(indexable_models)} model(s)"
            )
        )
        self.stdout.write(
            self.style.SUCCESS(f"✓ Total documents synced: {total_synced:,}")
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"✓ Total time: {self._format_duration(total_time)}"
            )
        )

        if total_time > 0:
            avg_docs_per_sec = total_synced / total_time
            self.stdout.write(
                self.style.SUCCESS(
                    f"✓ Average throughput: {avg_docs_per_sec:.0f} docs/s"
                )
            )

        # Model timing breakdown
        if model_timings:
            self.stdout.write("")
            self.stdout.write(self.style.MIGRATE_LABEL("Timing breakdown:"))
            for model_label, elapsed, count in sorted(
                model_timings, key=lambda x: x[1], reverse=True
            ):
                self.stdout.write(
                    f"  • {model_label}: {elapsed:.2f}s ({count:,} docs)"
                )

        if failed_models:
            self.stdout.write("")
            self.stdout.write(
                self.style.ERROR(f"✗ Failed models: {len(failed_models)}")
            )
            for model_label, error, elapsed in failed_models:
                self.stdout.write(
                    self.style.ERROR(
                        f"  • {model_label} (after {elapsed:.2f}s): {error}"
                    )
                )

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 70))

        if failed_models:
            raise Exception(f"Failed to sync {len(failed_models)} model(s)")

    def _format_duration(self, seconds):
        """Format duration in a human-readable way."""
        if seconds < 60:
            return f"{seconds:.2f}s"
        elif seconds < 3600:
            minutes = int(seconds // 60)
            secs = seconds % 60
            return f"{minutes}m {secs:.1f}s"
        else:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            secs = seconds % 60
            return f"{hours}h {minutes}m {secs:.0f}s"

    def _create_progress_bar(self, percent, width=30):
        """Create a visual progress bar."""
        filled = int(width * percent / 100)
        bar = "█" * filled + "░" * (width - filled)
        return bar

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
        from django.db import connection

        tasks = []
        total_count = 0
        batch_count = 0

        try:
            if connection.connection and connection.in_atomic_block:
                connection.close()
                close_old_connections()

            if hasattr(Model, "get_meilisearch_queryset"):
                queryset = Model.get_meilisearch_queryset()
            else:
                queryset = Model.objects.all()

            total_records = queryset.count()

            if total_records == 0:
                self.stdout.write("  ├─ No records to sync")
                return 0

            total_batches = (total_records + batch_size - 1) // batch_size
            self.stdout.write(
                f"  ├─ Processing {total_records:,} record(s) in {total_batches:,} batches of {batch_size:,}"
            )

            batch_start = time.perf_counter()
            last_progress_time = batch_start

            for qs in batch_qs(queryset, batch_size):
                batch_count += 1

                try:
                    # Remove transaction wrapper for better performance
                    documents = [
                        self._serialize(m) for m in qs if m.meili_filter()
                    ]
                    total_count += len(documents)

                    if documents:
                        task = _client.get_index(
                            Model._meilisearch["index_name"]
                        ).add_documents(documents)
                        tasks.append(task)

                        # Progress indicator - show every batch for first 10, then every 5
                        current_time = time.perf_counter()
                        show_progress = (
                            batch_count <= 10
                            or batch_count % 5 == 0
                            or current_time - last_progress_time
                            >= 2.0  # At least every 2 seconds
                        )

                        if show_progress:
                            elapsed = current_time - batch_start
                            rate = total_count / elapsed if elapsed > 0 else 0
                            percent = (
                                (total_count / total_records * 100)
                                if total_records > 0
                                else 0
                            )

                            # Estimate time remaining
                            if rate > 0:
                                remaining_docs = total_records - total_count
                                eta_seconds = remaining_docs / rate
                                eta_str = self._format_duration(eta_seconds)
                            else:
                                eta_str = "calculating..."

                            progress_bar = self._create_progress_bar(
                                percent, width=30
                            )

                            self.stdout.write(
                                f"  ├─ [{progress_bar}] {percent:.1f}% | "
                                f"Batch {batch_count}/{total_batches} | "
                                f"{total_count:,}/{total_records:,} docs | "
                                f"{rate:.0f} docs/s | "
                                f"ETA: {eta_str}",
                                ending="\r",
                            )
                            self.stdout.flush()
                            last_progress_time = current_time

                    close_old_connections()

                except Exception as e:
                    # Clear progress line before showing error
                    self.stdout.write(" " * 120, ending="\r")
                    self.stdout.write(
                        self.style.WARNING(
                            f"  ├─ Warning: Batch {batch_count} failed: {e}"
                        )
                    )
                    close_old_connections()
                    continue

            # Clear progress line and show final batch summary
            self.stdout.write(" " * 120, ending="\r")

            batch_elapsed = time.perf_counter() - batch_start
            self.stdout.write(
                f"  ├─ Completed {batch_count} batch(es) in {batch_elapsed:.2f}s, "
                f"waiting for {len(tasks)} MeiliSearch task(s)..."
            )

            # Wait for all tasks
            task_start = time.perf_counter()
            last_task_progress_time = task_start

            for idx, task in enumerate(tasks, 1):
                try:
                    finished = _client.wait_for_task(task.task_uid)
                    if finished.status == "failed":
                        raise Exception(finished.error)

                    # Progress for task waiting - show more frequently for large task counts
                    current_time = time.perf_counter()
                    show_progress = (
                        idx <= 10
                        or idx % 10 == 0
                        or idx == len(tasks)
                        or current_time - last_task_progress_time
                        >= 3.0  # At least every 3 seconds
                    )

                    if len(tasks) > 10 and show_progress:
                        task_percent = (
                            (idx / len(tasks) * 100) if len(tasks) > 0 else 0
                        )
                        task_elapsed = current_time - task_start
                        task_rate = (
                            idx / task_elapsed if task_elapsed > 0 else 0
                        )

                        # Estimate time remaining for tasks
                        if task_rate > 0:
                            remaining_tasks = len(tasks) - idx
                            task_eta_seconds = remaining_tasks / task_rate
                            task_eta_str = self._format_duration(
                                task_eta_seconds
                            )
                        else:
                            task_eta_str = "calculating..."

                        task_progress_bar = self._create_progress_bar(
                            task_percent, width=20
                        )

                        self.stdout.write(
                            f"  ├─ Tasks: [{task_progress_bar}] {task_percent:.1f}% | "
                            f"{idx}/{len(tasks)} | "
                            f"{task_rate:.1f} tasks/s | "
                            f"ETA: {task_eta_str}",
                            ending="\r",
                        )
                        self.stdout.flush()
                        last_task_progress_time = current_time

                except Exception as e:
                    # Clear progress line before showing error
                    self.stdout.write(" " * 120, ending="\r")
                    self.stdout.write(
                        self.style.WARNING(
                            f"  ├─ Warning: Task {idx}/{len(tasks)} wait failed: {e}"
                        )
                    )

            # Clear task progress line
            if len(tasks) > 10:
                self.stdout.write(" " * 120, ending="\r")

            task_time = time.perf_counter() - task_start
            if task_time > 0.1:  # Only show if significant
                self.stdout.write(
                    f"  ├─ All tasks completed in {task_time:.2f}s"
                )

            return total_count
        except Exception as e:
            close_old_connections()
            raise Exception(f"Failed to sync model: {e}")

    def _update_settings(self, Model):
        """Update settings for a single model's index using the model's method."""
        Model.update_meili_settings()

    def _serialize(self, model: IndexMixin) -> dict:
        """Serialize a model instance for MeiliSearch."""
        serialized = model.meili_serialize()
        pk = model._meta.pk.value_to_string(model)
        return serialized | {"id": pk, "pk": pk}
