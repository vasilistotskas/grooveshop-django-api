from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase

from meili.management.commands.meilisearch_drop import Command


class TestClearMeiliSearchCommand(TestCase):
    def setUp(self):
        self.command = Command()

    def test_help_message(self):
        assert (
            self.command.help
            == "Clears all MeiliSearch indexes and data (equivalent to clearing the MeiliSearch database)"
        )

    @patch("meili.management.commands.meilisearch_drop._client")
    @patch("builtins.input", return_value="yes")
    def test_meilisearch_drop_success(self, mock_input, mock_client):
        mock_index1 = MagicMock()
        mock_index1.uid = "test_index_1"
        mock_index2 = MagicMock()
        mock_index2.uid = "test_index_2"

        mock_client.get_indexes.return_value = [mock_index1, mock_index2]

        mock_task = MagicMock()
        mock_task.task_uid = "task_123"
        mock_client.client.delete_index.return_value = mock_task

        mock_finished_task = MagicMock()
        mock_finished_task.status = "succeeded"
        mock_client.wait_for_task.return_value = mock_finished_task

        output = StringIO()
        call_command("meilisearch_drop", stdout=output)

        output_content = output.getvalue()
        assert "Found 2 indexes to delete" in output_content
        assert "Deleted index: test_index_1" in output_content
        assert "Deleted index: test_index_2" in output_content
        assert "Successfully deleted 2 indexes" in output_content

    @patch("meili.management.commands.meilisearch_drop._client")
    def test_meilisearch_drop_force_flag(self, mock_client):
        mock_client.get_indexes.return_value = []

        output = StringIO()
        call_command("meilisearch_drop", "--force", stdout=output)

        output_content = output.getvalue()
        assert "No indexes found in MeiliSearch" in output_content

    @patch("meili.management.commands.meilisearch_drop._client")
    @patch("builtins.input", return_value="no")
    def test_meilisearch_drop_cancelled(self, mock_input, mock_client):
        output = StringIO()
        call_command("meilisearch_drop", stdout=output)

        output_content = output.getvalue()
        assert "Operation cancelled" in output_content

    @patch("meili.management.commands.meilisearch_drop._client")
    @patch("builtins.input", return_value="yes")
    def test_meilisearch_drop_with_failed_deletion(
        self, mock_input, mock_client
    ):
        mock_index = MagicMock()
        mock_index.uid = "test_index"
        mock_client.get_indexes.return_value = [mock_index]

        mock_task = MagicMock()
        mock_task.task_uid = "task_123"
        mock_client.client.delete_index.return_value = mock_task

        mock_finished_task = MagicMock()
        mock_finished_task.status = "failed"
        mock_finished_task.error = "Deletion failed"
        mock_client.wait_for_task.return_value = mock_finished_task

        output = StringIO()
        stderr = StringIO()
        call_command("meilisearch_drop", stdout=output, stderr=stderr)

        stderr_content = stderr.getvalue()
        assert (
            "Failed to delete index 'test_index': Deletion failed"
            in stderr_content
        )

    @patch("meili.management.commands.meilisearch_drop._client")
    @patch("builtins.input", return_value="yes")
    def test_meilisearch_drop_with_exception(self, mock_input, mock_client):
        mock_client.get_indexes.side_effect = Exception("Connection error")

        output = StringIO()
        stderr = StringIO()
        call_command("meilisearch_drop", stdout=output, stderr=stderr)

        stderr_content = stderr.getvalue()
        assert "Error clearing MeiliSearch: Connection error" in stderr_content

    @patch("meili.management.commands.meilisearch_drop._client")
    @patch("meili.management.commands.meilisearch_drop.apps")
    @patch("builtins.input", return_value="yes")
    def test_meilisearch_drop_with_recreate(
        self, mock_input, mock_apps, mock_client
    ):
        mock_index = MagicMock()
        mock_index.uid = "test_index"
        mock_client.get_indexes.return_value = [mock_index]

        mock_task = MagicMock()
        mock_task.task_uid = "task_123"
        mock_client.client.delete_index.return_value = mock_task

        mock_finished_task = MagicMock()
        mock_finished_task.status = "succeeded"
        mock_client.wait_for_task.return_value = mock_finished_task

        from meili.models import IndexMixin

        mock_model = MagicMock()
        mock_model.__mro__ = (IndexMixin, object)
        mock_model.__name__ = "TestModel"
        mock_model._meilisearch = {
            "index_name": "test_index",
            "primary_key": "pk",
        }

        mock_app_config = MagicMock()
        mock_app_config.get_models.return_value = [mock_model]
        mock_apps.get_app_configs.return_value = [mock_app_config]

        output = StringIO()
        call_command("meilisearch_drop", "--recreate", stdout=output)

        output_content = output.getvalue()
        assert "Recreating indexes..." in output_content
        assert "Recreated index: test_index" in output_content
