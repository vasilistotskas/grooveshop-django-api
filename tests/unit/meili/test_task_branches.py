"""Branch coverage for the Meilisearch Celery tasks (client mocked).

Covers ``meili/tasks.py``: ``index_document_task`` (missing instance,
successful add, failed Meilisearch task, swallowed removal failure),
``delete_document_task`` (success, document-not-found, failure) and
``reindex_model_task`` (clear-first, batching, filtering, partial failure).
The Meilisearch client is the only thing mocked.
"""

from __future__ import annotations

from itertools import chain, count, repeat
from unittest.mock import MagicMock, patch

import pytest

from meili.exceptions import MeiliTaskFailed
from meili.tasks import (
    delete_document_task,
    index_document_task,
    reindex_model_task,
)
from product.factories.product import ProductFactory
from product.models.product import ProductTranslation


def _client(*, statuses=(), error=None):
    """A Meilisearch client double.

    The first Meilisearch tasks finish with ``statuses`` (carrying
    ``error``); every later one succeeds. ``add_documents`` hands out
    task uids 100, 101, ...
    """
    index = MagicMock()
    index.add_documents.side_effect = (
        MagicMock(task_uid=uid) for uid in count(100)
    )
    index.delete_document.return_value = MagicMock(task_uid=7)
    index.delete_all_documents.return_value = MagicMock(task_uid=5)
    client = MagicMock()
    client.get_index.return_value = index
    client.wait_for_task.side_effect = chain(
        (MagicMock(status=status, error=error) for status in statuses),
        repeat(MagicMock(status="succeeded", error=None)),
    )
    return client, index


def _product_translation(**product_kwargs) -> ProductTranslation:
    product = ProductFactory(num_images=0, num_reviews=0, **product_kwargs)
    translation = product.translations.order_by("pk").first()
    assert translation is not None
    return translation


@pytest.mark.django_db
class TestIndexDocumentTask:
    def test_missing_instance_is_skipped_without_touching_meilisearch(self):
        client, _index = _client()

        with patch("meili._client.client", client):
            result = index_document_task(
                "product", "producttranslation", 987654321
            )

        assert result == {"status": "skipped", "reason": "instance_not_found"}
        client.get_index.assert_not_called()

    def test_qualifying_instance_is_added_with_string_id_and_pk(self):
        translation = _product_translation(active=True)
        client, index = _client()

        with patch("meili._client.client", client):
            result = index_document_task(
                "product", "producttranslation", translation.pk
            )

        assert result == {
            "status": "success",
            "index": "ProductTranslation",
            "document_id": str(translation.pk),
            "task_uid": 100,
        }
        client.get_index.assert_called_once_with("ProductTranslation")
        (documents,), _ = index.add_documents.call_args
        assert len(documents) == 1
        assert documents[0]["id"] == str(translation.pk)
        assert documents[0]["pk"] == str(translation.pk)
        assert documents[0]["language_code"] == translation.language_code
        assert "_geo" not in documents[0]
        client.wait_for_task.assert_called_once_with(100, timeout_in_ms=5000)

    def test_failed_meilisearch_task_raises_so_celery_retries(self):
        translation = _product_translation(active=True)
        client, _index = _client(statuses=("failed",), error="disk full")

        with (
            patch("meili._client.client", client),
            pytest.raises(MeiliTaskFailed) as exc_info,
        ):
            index_document_task("product", "producttranslation", translation.pk)

        assert exc_info.value.operation == "index"
        assert exc_info.value.error == "disk full"

    def test_add_documents_transport_error_propagates(self):
        translation = _product_translation(active=True)
        client, index = _client()
        index.add_documents.side_effect = ConnectionError("engine down")

        with (
            patch("meili._client.client", client),
            pytest.raises(ConnectionError, match="engine down"),
        ):
            index_document_task("product", "producttranslation", translation.pk)

    def test_failed_removal_of_filtered_out_document_is_swallowed(self):
        translation = _product_translation(active=False)
        client, index = _client()
        index.delete_document.side_effect = ConnectionError("engine down")

        with patch("meili._client.client", client):
            result = index_document_task(
                "product", "producttranslation", translation.pk
            )

        assert result == {"status": "removed", "reason": "filtered_out"}
        index.delete_document.assert_called_once_with(str(translation.pk))
        client.wait_for_task.assert_not_called()


class TestDeleteDocumentTask:
    def test_successful_delete_reports_the_task(self):
        client, index = _client()

        with patch("meili._client.client", client):
            result = delete_document_task("acme__ProductTranslation", "42")

        assert result == {
            "status": "success",
            "index": "acme__ProductTranslation",
            "document_id": "42",
            "task_uid": 7,
        }
        client.get_index.assert_called_once_with("acme__ProductTranslation")
        index.delete_document.assert_called_once_with("42")
        client.wait_for_task.assert_called_once_with(7, timeout_in_ms=5000)

    def test_document_not_found_is_skipped_not_raised(self):
        client, _index = _client(
            statuses=("failed",),
            error={"code": "document_not_found", "message": "gone"},
        )

        with patch("meili._client.client", client):
            result = delete_document_task("ProductTranslation", "42")

        assert result == {"status": "skipped", "reason": "document_not_found"}

    def test_any_other_failure_raises(self):
        client, _index = _client(
            statuses=("failed",), error={"code": "index_not_found"}
        )

        with (
            patch("meili._client.client", client),
            pytest.raises(MeiliTaskFailed) as exc_info,
        ):
            delete_document_task("ProductTranslation", "42")

        assert exc_info.value.operation == "delete"
        assert exc_info.value.error == {"code": "index_not_found"}


@pytest.mark.django_db
class TestReindexModelTask:
    def _run(self, client, **kwargs):
        with (
            patch("meili._client.client", client),
            patch.object(reindex_model_task, "update_state") as update_state,
        ):
            result = reindex_model_task(
                "product", "producttranslation", **kwargs
            )
        return result, update_state

    def test_indexes_qualifying_rows_in_batches_and_counts_filtered(self):
        active = [_product_translation(active=True) for _ in range(2)]
        _product_translation(active=False)
        expected_ids = {
            str(pk)
            for pk in ProductTranslation.objects.filter(
                master__active=True, master__is_deleted=False
            ).values_list("pk", flat=True)
        }
        total = ProductTranslation.objects.count()
        assert {str(t.pk) for t in active} <= expected_ids
        client, index = _client()

        result, update_state = self._run(client, batch_size=2)

        indexed_ids = {
            document["id"]
            for call in index.add_documents.call_args_list
            for document in call.args[0]
        }
        assert indexed_ids == expected_ids
        assert all(
            len(call.args[0]) <= 2
            for call in index.add_documents.call_args_list
        )
        assert result == {
            "status": "success",
            "index": "ProductTranslation",
            "total_indexed": len(expected_ids),
            "total_filtered": total - len(expected_ids),
            "total_records": total,
        }
        index.delete_all_documents.assert_not_called()
        # Progress is reported once per FULL batch.
        assert update_state.call_count == total // 2
        assert update_state.call_args_list[0].kwargs == {
            "state": "PROGRESS",
            "meta": {
                "current": 2,
                "total": total,
                "percent": int(2 / total * 100),
            },
        }

    def test_clear_first_empties_the_index_before_indexing(self):
        _product_translation(active=True)
        client, index = _client()

        result, _ = self._run(client, clear_first=True)

        assert result["status"] == "success"
        index.delete_all_documents.assert_called_once_with()
        assert client.wait_for_task.call_args_list[0].args == (5,)

    def test_failed_clear_aborts_before_indexing(self):
        _product_translation(active=True)
        client, index = _client(statuses=("failed",), error="locked")

        with pytest.raises(MeiliTaskFailed) as exc_info:
            self._run(client, clear_first=True)

        assert exc_info.value.operation == "clear index"
        index.add_documents.assert_not_called()

    def test_a_failed_batch_task_raises_partial_failure(self):
        _product_translation(active=True)
        client, _index = _client(statuses=("failed",), error="bad doc")

        with pytest.raises(RuntimeError) as exc_info:
            self._run(client, batch_size=1000)

        message = str(exc_info.value)
        assert "partial failure for ProductTranslation" in message
        assert "1 Meilisearch task(s) failed" in message
        assert "'task_uid': 100" in message
        assert "bad doc" in message
