"""Tests for convergent resync pruning (G0192).

meilisearch_sync_all_indexes was upsert-only: documents that were deleted or
that no longer pass meili_filter lingered in the index forever. _sync_model now
prunes any index document whose primary key was not synced this run.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from meili.management.commands.meilisearch_sync_all_indexes import Command


class _FakeModel:
    _meilisearch = {"index_name": "products", "primary_key": "pk"}


def _make_index(index_pks, *, page_size=1000):
    """Fake Meilisearch index whose get_documents pages over ``index_pks``."""
    docs = [SimpleNamespace(pk=pk) for pk in index_pks]

    def get_documents(params):
        offset = params["offset"]
        limit = params["limit"]
        return SimpleNamespace(results=docs[offset : offset + limit])

    index = MagicMock()
    index.get_documents.side_effect = get_documents
    index.delete_documents.return_value = SimpleNamespace(task_uid=7)
    return index


def _run_prune(index, synced_pks):
    command = Command()
    with patch(
        "meili.management.commands.meilisearch_sync_all_indexes._client"
    ) as mock_client:
        mock_client.get_index.return_value = index
        mock_client.wait_for_task.return_value = SimpleNamespace(
            status="succeeded", error=None
        )
        command._prune_stale_documents(_FakeModel, synced_pks)
    return index


class TestConvergentPrune:
    def test_deletes_orphaned_documents(self):
        index = _make_index(["1", "2", "3"])
        _run_prune(index, {"1", "2"})

        index.delete_documents.assert_called_once()
        deleted = set(index.delete_documents.call_args[0][0])
        assert deleted == {"3"}

    def test_no_delete_when_index_matches_source(self):
        index = _make_index(["1", "2"])
        _run_prune(index, {"1", "2"})

        index.delete_documents.assert_not_called()

    def test_empty_source_deletes_every_document(self):
        index = _make_index(["1", "2", "3"])
        _run_prune(index, set())

        index.delete_documents.assert_called_once()
        deleted = set(index.delete_documents.call_args[0][0])
        assert deleted == {"1", "2", "3"}

    def test_prunes_across_paginated_index(self):
        # 2500 docs force three get_documents pages at page_size 1000.
        index = _make_index([str(i) for i in range(2500)])
        synced = {str(i) for i in range(2500) if i % 2 == 0}
        _run_prune(index, synced)

        assert index.get_documents.call_count == 3
        deleted = set(index.delete_documents.call_args[0][0])
        assert deleted == {str(i) for i in range(2500) if i % 2 == 1}
