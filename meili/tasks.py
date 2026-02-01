"""
Celery tasks for asynchronous Meilisearch indexing.

These tasks handle document indexing and deletion in the background,
improving response times for write operations.
"""

import logging
from typing import Any

from celery import Task, shared_task
from django.apps import apps
from django.conf import settings

logger = logging.getLogger(__name__)


class MeiliTask(Task):
    """Base task class with retry configuration for Meilisearch operations."""

    autoretry_for = (Exception,)
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
    queue="meilisearch",
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
    from meili._client import client as _client  # noqa: PLC0415

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

        # Check if it should be indexed
        if not instance.meili_filter():
            logger.debug(f"Model {app_label}.{model_name} pk={pk} filtered out")
            return {"status": "skipped", "reason": "filtered_out"}

        # Serialize and index
        serialized = instance.meili_serialize()
        document_pk = _get_document_pk(instance)
        geo = (
            instance.meili_geo()
            if instance._meilisearch["supports_geo"]
            else None
        )

        document = {
            **serialized,
            "id": document_pk,
            "pk": instance._meta.pk.value_to_string(instance),
        }
        if geo:
            document["_geo"] = geo

        index_name = instance._meilisearch["index_name"]
        task = _client.get_index(index_name).add_documents([document])

        # Wait for task completion
        finished = _client.wait_for_task(task.task_uid)

        if finished.status == "failed":
            raise Exception(f"Meilisearch indexing failed: {finished.error}")

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
    name="meili.delete_document",
    queue="meilisearch",
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
    from meili._client import client as _client  # noqa: PLC0415

    try:
        task = _client.get_index(index_name).delete_document(document_pk)

        # Wait for task completion
        finished = _client.wait_for_task(task.task_uid)

        if finished.status == "failed":
            # Document not found is not an error
            if "document_not_found" in str(finished.error).lower():
                logger.debug(
                    f"Document {document_pk} not found in {index_name}"
                )
                return {"status": "skipped", "reason": "document_not_found"}
            raise Exception(f"Meilisearch deletion failed: {finished.error}")

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
    name="meili.bulk_index",
    queue="meilisearch",
    soft_time_limit=3600,  # 1 hour soft limit
    time_limit=3900,  # 1 hour 5 min hard limit
)
def bulk_index_task(
    self,
    app_label: str,
    model_name: str,
    pk_list: list[Any],
    batch_size: int | None = None,
) -> dict:
    """
    Bulk index multiple documents to Meilisearch.

    Args:
        app_label: Django app label
        model_name: Model name
        pk_list: List of primary keys to index
        batch_size: Optional batch size (defaults to settings)

    Returns:
        Dict with task status and statistics
    """
    from meili._client import client as _client  # noqa: PLC0415

    batch_size = batch_size or settings.MEILISEARCH.get(
        "DEFAULT_BATCH_SIZE", 1000
    )

    try:
        model_class = apps.get_model(app_label, model_name)
        index_name = model_class._meilisearch["index_name"]

        # Fetch all instances
        if hasattr(model_class, "get_meilisearch_queryset"):
            queryset = model_class.get_meilisearch_queryset().filter(
                pk__in=pk_list
            )
        else:
            queryset = model_class.objects.filter(pk__in=pk_list)

        total_indexed = 0
        total_filtered = 0
        tasks = []

        # Process in batches
        instances = list(queryset)
        for i in range(0, len(instances), batch_size):
            batch = instances[i : i + batch_size]
            documents = []

            for instance in batch:
                if not instance.meili_filter():
                    total_filtered += 1
                    continue

                serialized = instance.meili_serialize()
                document_pk = _get_document_pk(instance)
                geo = (
                    instance.meili_geo()
                    if instance._meilisearch["supports_geo"]
                    else None
                )

                document = {
                    **serialized,
                    "id": document_pk,
                    "pk": instance._meta.pk.value_to_string(instance),
                }
                if geo:
                    document["_geo"] = geo

                documents.append(document)

            if documents:
                task = _client.get_index(index_name).add_documents(documents)
                tasks.append(task)
                total_indexed += len(documents)

        # Wait for all tasks
        failed_tasks = []
        for task in tasks:
            finished = _client.wait_for_task(task.task_uid)
            if finished.status == "failed":
                failed_tasks.append(
                    {"task_uid": task.task_uid, "error": str(finished.error)}
                )

        if failed_tasks:
            logger.warning(f"Some bulk indexing tasks failed: {failed_tasks}")

        logger.info(
            f"Bulk indexed {total_indexed} documents to {index_name}, "
            f"{total_filtered} filtered out, {len(failed_tasks)} tasks failed"
        )

        return {
            "status": "success" if not failed_tasks else "partial",
            "index": index_name,
            "total_indexed": total_indexed,
            "total_filtered": total_filtered,
            "failed_tasks": failed_tasks,
        }

    except Exception as e:
        logger.error(f"Error bulk indexing {app_label}.{model_name}: {e}")
        raise


@shared_task(
    base=MeiliTask,
    bind=True,
    name="meili.reindex_model",
    queue="meilisearch",
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
    from meili._client import client as _client  # noqa: PLC0415

    batch_size = batch_size or settings.MEILISEARCH.get(
        "DEFAULT_BATCH_SIZE", 1000
    )

    try:
        model_class = apps.get_model(app_label, model_name)
        index_name = model_class._meilisearch["index_name"]

        # Optionally clear the index first
        if clear_first:
            task = _client.get_index(index_name).delete_all_documents()
            finished = _client.wait_for_task(task.task_uid)
            if finished.status == "failed":
                raise Exception(f"Failed to clear index: {finished.error}")
            logger.info(f"Cleared index {index_name}")

        # Get queryset
        if hasattr(model_class, "get_meilisearch_queryset"):
            queryset = model_class.get_meilisearch_queryset()
        else:
            queryset = model_class.objects.all()

        total_count = queryset.count()
        total_indexed = 0
        total_filtered = 0
        tasks = []

        # Process in batches
        for start in range(0, total_count, batch_size):
            batch = queryset[start : start + batch_size]
            documents = []

            for instance in batch:
                if not instance.meili_filter():
                    total_filtered += 1
                    continue

                serialized = instance.meili_serialize()
                document_pk = _get_document_pk(instance)
                geo = (
                    instance.meili_geo()
                    if instance._meilisearch["supports_geo"]
                    else None
                )

                document = {
                    **serialized,
                    "id": document_pk,
                    "pk": instance._meta.pk.value_to_string(instance),
                }
                if geo:
                    document["_geo"] = geo

                documents.append(document)

            if documents:
                task = _client.get_index(index_name).add_documents(documents)
                tasks.append(task)
                total_indexed += len(documents)

            # Update task progress
            progress = min(100, int((start + batch_size) / total_count * 100))
            self.update_state(
                state="PROGRESS",
                meta={
                    "current": start + batch_size,
                    "total": total_count,
                    "percent": progress,
                },
            )

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

        return {
            "status": "success" if not failed_tasks else "partial",
            "index": index_name,
            "total_indexed": total_indexed,
            "total_filtered": total_filtered,
            "total_records": total_count,
            "failed_tasks": failed_tasks,
        }

    except Exception as e:
        logger.error(f"Error reindexing {app_label}.{model_name}: {e}")
        raise


def _get_document_pk(instance) -> str:
    """Extract the document primary key based on model configuration."""
    if instance._meilisearch["primary_key"] == "pk":
        return instance._meta.pk.value_to_string(instance)
    return instance._meta.get_field(
        instance._meilisearch["primary_key"]
    ).value_to_string(instance)
