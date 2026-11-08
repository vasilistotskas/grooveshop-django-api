"""Queueing index documents in batches, and holding them back to flush once.

One ``meili.index_document`` task per document made a bulk change cost a
broker message and an engine task per row. :func:`queue_documents` sends
them as ``meili.index_documents`` tasks of :data:`INDEX_TASK_BATCH_SIZE`
documents (one add and one delete call each).

A writer that saves many rows (the catalogue import) wraps the work in
:func:`collect_reindex`: while it is open, the save signals hand their
document to :func:`collect_if_active` instead of queueing a task, and the
whole set is queued once on exit, deduplicated.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from tenant.celery import dispatch_on_commit

# Documents one ``meili.index_documents`` task adds or removes: a task holds
# a Celery worker while it serializes and waits for the engine, so the batch
# is smaller than a sync command's page (``MEILISEARCH["DEFAULT_BATCH_SIZE"]``).
INDEX_TASK_BATCH_SIZE = 500

type DocumentKey = tuple[str, str]

_collector: ContextVar[dict[DocumentKey, set[Any]] | None] = ContextVar(
    "meili_reindex_collector", default=None
)


def queue_documents(app_label: str, model_name: str, pks: Iterable[Any]) -> int:
    """Queue ``pks`` for indexing after the commit; return how many.

    While :func:`collect_reindex` is open nothing is queued: the pks join
    the set it flushes, and the return value is 0.
    """
    from meili.tasks import index_documents_task

    pks = list(pks)
    if collect_if_active(app_label, model_name, pks):
        return 0
    size = INDEX_TASK_BATCH_SIZE
    for start in range(0, len(pks), size):
        dispatch_on_commit(
            index_documents_task,
            kwargs={
                "app_label": app_label,
                "model_name": model_name,
                "pks": pks[start : start + size],
            },
        )
    return len(pks)


def collect_if_active(
    app_label: str, model_name: str, pks: Iterable[Any]
) -> bool:
    """Hold ``pks`` for the open :func:`collect_reindex`, if there is one.

    Returns whether it took them; the caller queues its own task otherwise.
    """
    collected = _collector.get()
    if collected is None:
        return False
    collected[app_label, model_name].update(pks)
    return True


@contextmanager
def collect_reindex() -> Generator[None]:
    """Gather every document to index in the block and queue it once on exit.

    The flush runs when the block raises too: rows saved before the error
    are committed, and their documents must not stay stale. Nested, the
    inner block joins the outer one, which flushes.
    """
    if _collector.get() is not None:
        yield
        return
    collected: dict[DocumentKey, set[Any]] = defaultdict(set)
    token = _collector.set(collected)
    try:
        yield
    finally:
        _collector.reset(token)
        for (app_label, model_name), pks in collected.items():
            queue_documents(app_label, model_name, sorted(pks))
