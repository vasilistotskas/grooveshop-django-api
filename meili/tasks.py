"""
Celery tasks for asynchronous Meilisearch indexing.

These tasks handle document indexing and deletion in the background,
improving response times for write operations.
"""

import logging
from typing import Any

from celery import shared_task
from django.apps import apps
from django.conf import settings

from core.retry import SEARCH_TRANSIENT_ERRORS
from meili.exceptions import MeiliTaskFailed
from tenant.celery import TenantTask

logger = logging.getLogger(__name__)

# How long the one-document tasks (index, remove) wait for the engine.
# Deliberately short and not ``MEILISEARCH["TASK_TIMEOUT_MS"]``: a timeout
# here raises and ``MeiliTask`` retries with backoff, which frees the
# Celery worker slot while the engine's queue is backlogged. The bulk
# paths (sync commands, ``sync_index_task``) wait the configured time.
SINGLE_DOCUMENT_WAIT_MS = 5_000


class MeiliTask(TenantTask):
    """Base task class for Meilisearch operations.

    Inherits tenant schema propagation (apply_async stamps _schema_name
    header; __call__ wraps execution in schema_context) from TenantTask so
    that indexing operations always run against the correct tenant's schema.

    Adds Meilisearch-specific retry configuration and structured logging.
    """

    # What a retry can fix: the server unreachable or too slow, the DB or
    # broker briefly gone. ``MeiliTaskFailed`` (the engine rejected the
    # write) is not here: it fails the same way every time.
    autoretry_for = SEARCH_TRANSIENT_ERRORS
    retry_backoff = True
    retry_backoff_max = 600  # 10 minutes max backoff
    retry_jitter = True
    max_retries = 5

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error(
            f"Meilisearch task {self.name} failed after {self.max_retries} retries. "
            f"Task ID: {task_id}, Error: {exc}"
        )

    def on_retry(self, exc, task_id, args, kwargs, einfo):
        logger.warning(
            f"Meilisearch task {self.name} retrying. Task ID: {task_id}, Error: {exc}"
        )


@shared_task(
    base=MeiliTask,
    bind=True,
    name="meili.index_document",
)
def index_document_task(
    self,
    app_label: str,
    model_name: str,
    pk: Any,
) -> dict:
    """
    Index a single document to Meilisearch.

    Args:
        app_label: Django app label (e.g., 'product')
        model_name: Model name (e.g., 'producttranslation')
        pk: Primary key of the model instance

    Returns:
        Dict with task status and details
    """
    from meili._client import client as _client

    try:
        # Get the model class
        model_class = apps.get_model(app_label, model_name)

        # Fetch the instance
        try:
            instance = model_class.objects.get(pk=pk)
        except model_class.DoesNotExist:
            logger.warning(
                f"Model {app_label}.{model_name} with pk={pk} not found, skipping indexing"
            )
            return {"status": "skipped", "reason": "instance_not_found"}

        # Check if it should be indexed. If it no longer qualifies (e.g. the
        # product was deactivated or soft-deleted), REMOVE any existing
        # document instead of leaving stale, still-searchable data behind.
        # This is the update path: post_save fires this task, and a save that
        # flips meili_filter() to False must delete the document.
        if not instance.meili_filter():
            index_name = instance.get_meili_index_name()
            document_pk = _get_document_pk(instance)
            logger.debug(
                f"Model {app_label}.{model_name} pk={pk} filtered out; "
                f"removing document {document_pk} from {index_name}"
            )
            try:
                task = _client.get_index(index_name).delete_document(
                    document_pk
                )
                _client.wait_for_task(
                    task.task_uid, timeout_in_ms=SINGLE_DOCUMENT_WAIT_MS
                )
            except SEARCH_TRANSIENT_ERRORS:
                # The document may still be searchable: let ``MeiliTask``
                # retry with backoff instead of reporting it removed.
                raise
            except Exception as exc:
                # Not a failure a retry fixes (the index is gone, the
                # engine rejected the request): nothing to retry, and the
                # document is not reachable through a missing index.
                logger.warning(
                    f"Failed to remove filtered-out document {document_pk} "
                    f"from {index_name}: {exc}"
                )
            return {"status": "removed", "reason": "filtered_out"}

        # Serialize and index
        document = _build_document(instance)
        document_pk = document["id"]

        index_name = type(instance).get_meili_index_name()
        task = _client.get_index(index_name).add_documents([document])

        # Bounded wait — don't hold the Celery worker slot indefinitely
        # when Meilisearch queues are backlogged. A timeout here raises,
        # which MeiliTask.autoretry_for catches and reschedules with
        # exponential backoff.
        finished = _client.wait_for_task(
            task.task_uid, timeout_in_ms=SINGLE_DOCUMENT_WAIT_MS
        )

        if finished.status == "failed":
            raise MeiliTaskFailed(finished.error, operation="index")

        logger.info(f"Indexed {app_label}.{model_name} pk={pk} to {index_name}")

        return {
            "status": "success",
            "index": index_name,
            "document_id": document_pk,
            "task_uid": task.task_uid,
        }

    except Exception as e:
        logger.error(f"Error indexing {app_label}.{model_name} pk={pk}: {e}")
        raise


@shared_task(
    base=MeiliTask,
    bind=True,
    name="meili.index_documents",
)
def index_documents_task(
    self,
    app_label: str,
    model_name: str,
    pks: list[Any],
) -> dict:
    """
    Index a batch of documents with one add and one delete call.

    The batch form of ``index_document_task``: each row is loaded from the
    model's indexing queryset, the ones that qualify (``meili_filter()``)
    are added in ONE ``add_documents`` call and the ones that no longer
    qualify are removed in ONE delete-by-filter call on the primary key
    (``delete_documents_by_primary_key``), so N documents
    cost two engine tasks instead of N. A pk with no row is skipped, as in
    the single-document task.

    Waits for the engine as long as the bulk paths do
    (``MEILISEARCH["TASK_TIMEOUT_MS"]``): a batch takes longer than one
    document. A timeout raises, and ``MeiliTask``
    retries the whole batch: adding and deleting are both idempotent.

    Args:
        app_label: Django app label (e.g., 'product')
        model_name: Model name (e.g., 'producttranslation')
        pks: Primary keys of the model instances

    Returns:
        Dict with the counts of documents added, removed and skipped
    """
    from meili._client import client as _client

    model_class = apps.get_model(app_label, model_name)
    index = _client.get_index(model_class.get_meili_index_name())

    queryset = model_class.get_meilisearch_queryset().filter(pk__in=pks)
    documents: list[dict] = []
    removed: list[str] = []
    for instance in queryset:
        if instance.meili_filter():
            documents.append(_build_document(instance))
        else:
            removed.append(_get_document_pk(instance))

    # Both calls are attempted whatever the first one does: a bad
    # document in the add must not leave the deactivated products of the
    # same batch searchable. The first failure is raised afterwards (so
    # ``MeiliTask`` decides on a retry); the batch is idempotent.
    failures: list[Exception] = []
    if documents:
        try:
            finished = _client.wait_for_task(
                index.add_documents(documents).task_uid
            )
            if finished.status == "failed":
                raise MeiliTaskFailed(finished.error, operation="index")
        except Exception as exc:
            failures.append(exc)
    if removed:
        try:
            finished = _client.wait_for_task(
                _client.delete_documents_by_primary_key(
                    model_class.get_meili_index_name(),
                    model_class._meilisearch["primary_key"],
                    removed,
                ).task_uid
            )
            if finished.status == "failed":
                raise MeiliTaskFailed(finished.error, operation="delete")
        except Exception as exc:
            failures.append(exc)
    if failures:
        for extra in failures[1:]:
            logger.error(
                f"Also failed on {app_label}.{model_name} batch: {extra}"
            )
        raise failures[0]

    skipped = len(pks) - len(documents) - len(removed)
    logger.info(
        f"Indexed {app_label}.{model_name} batch: {len(documents)} added, "
        f"{len(removed)} removed, {skipped} not found"
    )
    return {
        "status": "success",
        "added": len(documents),
        "removed": len(removed),
        "skipped": skipped,
    }


@shared_task(
    base=MeiliTask,
    bind=True,
    name="meili.delete_document",
)
def delete_document_task(
    self,
    index_name: str,
    document_pk: str,
) -> dict:
    """
    Delete a document from Meilisearch.

    Args:
        index_name: Name of the Meilisearch index
        document_pk: Primary key of the document to delete

    Returns:
        Dict with task status and details
    """
    from meili._client import client as _client

    try:
        task = _client.get_index(index_name).delete_document(document_pk)

        # Bounded wait — consistent with index_document_task timeout.
        finished = _client.wait_for_task(
            task.task_uid, timeout_in_ms=SINGLE_DOCUMENT_WAIT_MS
        )

        if finished.status == "failed":
            # Document not found is not an error
            if "document_not_found" in str(finished.error).lower():
                logger.debug(
                    f"Document {document_pk} not found in {index_name}"
                )
                return {"status": "skipped", "reason": "document_not_found"}
            raise MeiliTaskFailed(finished.error, operation="delete")

        logger.info(f"Deleted document {document_pk} from {index_name}")

        return {
            "status": "success",
            "index": index_name,
            "document_id": document_pk,
            "task_uid": task.task_uid,
        }

    except Exception as e:
        logger.error(
            f"Error deleting document {document_pk} from {index_name}: {e}"
        )
        raise


@shared_task(
    base=MeiliTask,
    bind=True,
    name="meili.reindex_model",
    soft_time_limit=7200,  # 2 hours soft limit
    time_limit=7500,  # 2 hours 5 min hard limit
)
def reindex_model_task(
    self,
    app_label: str,
    model_name: str,
    batch_size: int | None = None,
    clear_first: bool = False,
) -> dict:
    """
    Reindex all documents for a model.

    Args:
        app_label: Django app label
        model_name: Model name
        batch_size: Optional batch size
        clear_first: Whether to clear the index before reindexing

    Returns:
        Dict with task status and statistics
    """
    from meili._client import client as _client

    batch_size = batch_size or settings.MEILISEARCH.get(
        "DEFAULT_BATCH_SIZE", 1000
    )

    try:
        model_class = apps.get_model(app_label, model_name)
        index_name = model_class.get_meili_index_name()

        # Optionally clear the index first
        if clear_first:
            task = _client.get_index(index_name).delete_all_documents()
            finished = _client.wait_for_task(task.task_uid)
            if finished.status == "failed":
                raise MeiliTaskFailed(finished.error, operation="clear index")
            logger.info(f"Cleared index {index_name}")

        # Get queryset — force pk ordering so PostgreSQL slicing is
        # deterministic across batches. Without an explicit ORDER BY,
        # rows can be skipped or duplicated between slices.
        if hasattr(model_class, "get_meilisearch_queryset"):
            queryset = model_class.get_meilisearch_queryset()
        else:
            queryset = model_class.objects.all()
        queryset = queryset.order_by("pk")

        total_count = queryset.count()
        total_indexed = 0
        total_filtered = 0
        tasks = []

        def _process_batch(batch: list) -> None:
            nonlocal total_indexed, total_filtered
            documents = []
            for instance in batch:
                if not instance.meili_filter():
                    total_filtered += 1
                    continue

                documents.append(_build_document(instance))

            if documents:
                task = _client.get_index(index_name).add_documents(documents)
                tasks.append(task)
                total_indexed += len(documents)

        # Iterator-based batching avoids the LIMIT/OFFSET O(n²) scan:
        # each OFFSET N forces Postgres to scan and discard N rows before
        # returning the next batch.  .iterator() streams rows in a single
        # server-side cursor pass; we buffer them manually into chunks
        # matching batch_size before sending to Meilisearch.
        current_batch: list = []
        processed = 0
        for instance in queryset.iterator(chunk_size=batch_size):
            current_batch.append(instance)
            if len(current_batch) >= batch_size:
                _process_batch(current_batch)
                processed += len(current_batch)
                current_batch = []
                progress = min(
                    100,
                    int(processed / total_count * 100) if total_count else 100,
                )
                self.update_state(
                    state="PROGRESS",
                    meta={
                        "current": processed,
                        "total": total_count,
                        "percent": progress,
                    },
                )
        if current_batch:
            _process_batch(current_batch)
            processed += len(current_batch)

        # Wait for all tasks
        failed_tasks = []
        for task in tasks:
            finished = _client.wait_for_task(task.task_uid)
            if finished.status == "failed":
                failed_tasks.append(
                    {"task_uid": task.task_uid, "error": str(finished.error)}
                )

        logger.info(
            f"Reindexed {total_indexed} documents to {index_name}, "
            f"{total_filtered} filtered out"
        )

        if failed_tasks:
            raise RuntimeError(
                f"reindex_model_task partial failure for {index_name}: "
                f"{len(failed_tasks)} Meilisearch task(s) failed: "
                f"{failed_tasks}"
            )

        return {
            "status": "success",
            "index": index_name,
            "total_indexed": total_indexed,
            "total_filtered": total_filtered,
            "total_records": total_count,
        }

    except Exception as e:
        logger.error(f"Error reindexing {app_label}.{model_name}: {e}")
        raise


def _build_document(instance) -> dict:
    """The document Meilisearch stores for ``instance``."""
    document = {
        **instance.meili_serialize(),
        "id": _get_document_pk(instance),
        "pk": instance._meta.pk.value_to_string(instance),
    }
    if instance._meilisearch["supports_geo"]:
        geo = instance.meili_geo()
        if geo:
            document["_geo"] = geo
    return document


def _get_document_pk(instance) -> str:
    """Extract the document primary key based on model configuration."""
    if instance._meilisearch["primary_key"] == "pk":
        return instance._meta.pk.value_to_string(instance)
    return instance._meta.get_field(
        instance._meilisearch["primary_key"]
    ).value_to_string(instance)
