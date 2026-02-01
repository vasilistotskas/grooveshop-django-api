"""
Batch indexing utilities for Meilisearch.

Provides context managers and utilities for efficient bulk indexing operations,
following Meilisearch best practices for larger HTTP payloads.
"""

import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING, Generator

from django.conf import settings
from django.db.models.signals import post_delete, post_save

if TYPE_CHECKING:
    from meili.models import IndexMixin

logger = logging.getLogger(__name__)


class BatchIndexer:
    """
    Batch indexer for efficient bulk document operations.

    Collects documents and sends them in configurable batch sizes,
    following Meilisearch's recommendation for larger HTTP payloads.

    Example:
        with BatchIndexer() as indexer:
            for product in products:
                indexer.add(product)
        # Documents are automatically flushed on exit
    """

    def __init__(
        self,
        batch_size: int | None = None,
        wait_for_tasks: bool = True,
    ):
        """
        Initialize the batch indexer.

        Args:
            batch_size: Number of documents per batch (default from settings)
            wait_for_tasks: Whether to wait for Meilisearch tasks to complete
        """
        self.batch_size = batch_size or settings.MEILISEARCH.get(
            "DEFAULT_BATCH_SIZE", 1000
        )
        self.wait_for_tasks = wait_for_tasks
        self._documents: dict[str, list[dict]] = {}  # index_name -> documents
        self._tasks: list = []
        self._stats = {
            "total_added": 0,
            "total_deleted": 0,
            "total_filtered": 0,
            "batches_sent": 0,
            "failed_tasks": 0,
        }

    def add(self, instance: "IndexMixin") -> bool:
        """
        Add a document to the batch.

        Args:
            instance: Model instance to index

        Returns:
            True if document was added, False if filtered out
        """
        if not instance.meili_filter():
            self._stats["total_filtered"] += 1
            return False

        index_name = instance._meilisearch["index_name"]
        document = self._serialize_instance(instance)

        if index_name not in self._documents:
            self._documents[index_name] = []

        self._documents[index_name].append(document)
        self._stats["total_added"] += 1

        # Flush if batch size reached for this index
        if len(self._documents[index_name]) >= self.batch_size:
            self._flush_index(index_name)

        return True

    def add_many(self, instances: list["IndexMixin"]) -> int:
        """
        Add multiple documents to the batch.

        Args:
            instances: List of model instances to index

        Returns:
            Number of documents added (excluding filtered)
        """
        added = 0
        for instance in instances:
            if self.add(instance):
                added += 1
        return added

    def delete(self, instance: "IndexMixin") -> None:
        """
        Mark a document for deletion.

        Note: Deletions are processed immediately, not batched.
        """
        from meili._client import client as _client  # noqa: PLC0415

        index_name = instance._meilisearch["index_name"]
        pk = self._get_document_pk(instance)

        task = _client.get_index(index_name).delete_document(pk)
        self._tasks.append(task)
        self._stats["total_deleted"] += 1

    def flush(self) -> dict:
        """
        Flush all pending documents to Meilisearch.

        Returns:
            Statistics about the flush operation
        """
        for index_name in list(self._documents.keys()):
            self._flush_index(index_name)

        # Wait for all tasks if configured
        if self.wait_for_tasks:
            self._wait_for_tasks()

        return self.stats

    @property
    def stats(self) -> dict:
        """Return current statistics."""
        return self._stats.copy()

    def _flush_index(self, index_name: str) -> None:
        """Flush documents for a specific index."""
        from meili._client import client as _client  # noqa: PLC0415

        documents = self._documents.get(index_name, [])
        if not documents:
            return

        try:
            task = _client.get_index(index_name).add_documents(documents)
            self._tasks.append(task)
            self._stats["batches_sent"] += 1
            logger.debug(
                f"Sent batch of {len(documents)} documents to {index_name}"
            )
        except Exception as e:
            logger.error(f"Error sending batch to {index_name}: {e}")
            self._stats["failed_tasks"] += 1
            raise
        finally:
            self._documents[index_name] = []

    def _wait_for_tasks(self) -> None:
        """Wait for all pending Meilisearch tasks to complete."""
        from meili._client import client as _client  # noqa: PLC0415

        for task in self._tasks:
            try:
                finished = _client.wait_for_task(task.task_uid)
                if finished.status == "failed":
                    logger.error(
                        f"Meilisearch task {task.task_uid} failed: {finished.error}"
                    )
                    self._stats["failed_tasks"] += 1
            except Exception as e:
                logger.error(f"Error waiting for task {task.task_uid}: {e}")
                self._stats["failed_tasks"] += 1

        self._tasks = []

    def _serialize_instance(self, instance: "IndexMixin") -> dict:
        """Serialize a model instance for Meilisearch."""
        serialized = instance.meili_serialize()
        pk = self._get_document_pk(instance)
        geo = (
            instance.meili_geo()
            if instance._meilisearch["supports_geo"]
            else None
        )

        document = {
            **serialized,
            "id": pk,
            "pk": instance._meta.pk.value_to_string(instance),
        }
        if geo:
            document["_geo"] = geo

        return document

    def _get_document_pk(self, instance: "IndexMixin") -> str:
        """Extract the document primary key."""
        if instance._meilisearch["primary_key"] == "pk":
            return instance._meta.pk.value_to_string(instance)
        return instance._meta.get_field(
            instance._meilisearch["primary_key"]
        ).value_to_string(instance)

    def __enter__(self) -> "BatchIndexer":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is None:
            self.flush()
        else:
            logger.warning(
                f"BatchIndexer exiting with exception, {self._stats['total_added']} "
                f"documents may not have been indexed"
            )


@contextmanager
def suspend_indexing() -> Generator[None, None, None]:
    """
    Context manager to temporarily suspend automatic Meilisearch indexing.

    Useful for bulk operations where you want to control indexing manually.

    Example:
        with suspend_indexing():
            # Create many objects without triggering individual index operations
            for data in bulk_data:
                Product.objects.create(**data)

        # After the context, manually trigger bulk indexing
        with BatchIndexer() as indexer:
            indexer.add_many(Product.objects.filter(created_at__gte=start_time))
    """

    # Store original signal receivers
    original_save_receivers = post_save.receivers.copy()
    original_delete_receivers = post_delete.receivers.copy()

    # Disconnect all meili signal handlers
    meili_save_uids = [
        uid
        for uid in [r[0] for r in post_save.receivers]
        if isinstance(uid, str) and uid.startswith("meili_")
    ]
    meili_delete_uids = [
        uid
        for uid in [r[0] for r in post_delete.receivers]
        if isinstance(uid, str) and uid.startswith("meili_")
    ]

    for uid in meili_save_uids:
        post_save.disconnect(dispatch_uid=uid)
    for uid in meili_delete_uids:
        post_delete.disconnect(dispatch_uid=uid)

    try:
        yield
    finally:
        # Restore original receivers
        post_save.receivers = original_save_receivers
        post_delete.receivers = original_delete_receivers


@contextmanager
def batch_index_context(
    batch_size: int | None = None,
    wait_for_tasks: bool = True,
) -> Generator[BatchIndexer, None, None]:
    """
    Context manager combining suspended indexing with batch indexer.

    Suspends automatic indexing and provides a BatchIndexer for manual control.

    Example:
        with batch_index_context() as indexer:
            for data in bulk_data:
                product = Product.objects.create(**data)
                indexer.add(product)
        # All documents are flushed and indexed on exit
    """
    with suspend_indexing():
        indexer = BatchIndexer(
            batch_size=batch_size, wait_for_tasks=wait_for_tasks
        )
        try:
            yield indexer
        finally:
            indexer.flush()


def bulk_index_queryset(
    queryset,
    batch_size: int | None = None,
    progress_callback: callable | None = None,
) -> dict:
    """
    Bulk index a Django queryset to Meilisearch.

    Args:
        queryset: Django queryset of IndexMixin models
        batch_size: Documents per batch
        progress_callback: Optional callback(current, total) for progress updates

    Returns:
        Statistics about the indexing operation

    Example:
        stats = bulk_index_queryset(
            Product.objects.filter(active=True),
            progress_callback=lambda c, t: print(f"{c}/{t}")
        )
    """
    batch_size = batch_size or settings.MEILISEARCH.get(
        "DEFAULT_BATCH_SIZE", 1000
    )
    total = queryset.count()

    with BatchIndexer(batch_size=batch_size) as indexer:
        for i, instance in enumerate(queryset.iterator(chunk_size=batch_size)):
            indexer.add(instance)

            if progress_callback and (i + 1) % batch_size == 0:
                progress_callback(i + 1, total)

        if progress_callback:
            progress_callback(total, total)

        return indexer.stats
