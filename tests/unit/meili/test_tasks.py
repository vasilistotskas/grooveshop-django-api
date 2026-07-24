"""Unit tests for meili indexing tasks (Meilisearch client mocked)."""

from unittest.mock import MagicMock, patch

import pytest

from meili.tasks import index_document_task
from product.factories.product import ProductFactory


@pytest.mark.django_db
class TestIndexDocumentTaskRemovesFilteredOut:
    def test_deactivated_product_document_is_deleted_not_skipped(self):
        # An indexable instance that no longer matches meili_filter() (its
        # product was deactivated) must have its stale document removed from
        # the index, not silently skipped — otherwise it stays searchable.
        product = ProductFactory(active=False, num_images=0, num_reviews=0)
        translation = product.translations.first()
        assert translation is not None
        assert translation.meili_filter() is False

        mock_index = MagicMock()
        mock_index.delete_document.return_value = MagicMock(task_uid=1)
        mock_client = MagicMock()
        mock_client.get_index.return_value = mock_index
        mock_client.wait_for_task.return_value = MagicMock(status="succeeded")

        with patch("meili._client.client", mock_client):
            result = index_document_task(
                translation._meta.app_label,
                translation._meta.model_name,
                translation.pk,
            )

        assert result["status"] == "removed"
        assert result["reason"] == "filtered_out"
        mock_index.delete_document.assert_called_once()
        # The document is not (re-)added when it no longer qualifies.
        mock_index.add_documents.assert_not_called()
