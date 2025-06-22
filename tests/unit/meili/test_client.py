from unittest.mock import MagicMock, patch

import pytest

from meili._client import Client
from meili._client import client as global_client
from meili._settings import _MeiliSettings
from meili.dataclasses import MeiliIndexSettings


class TestMeiliClient:
    def setup_method(self):
        self.settings = _MeiliSettings(
            host="localhost",
            port=7700,
            https=False,
            master_key="test_key",
            timeout=10,
            sync=False,
            client_agents=("test-agent",),
            debug=False,
            offline=False,
            batch_size=1000,
        )

    @patch("meili._client._Client")
    def test_client_initialization(self, mock_client_class):
        mock_client_instance = MagicMock()
        mock_client_class.return_value = mock_client_instance

        client = Client(self.settings)

        assert client.client == mock_client_instance
        assert client.is_sync == self.settings.sync
        assert client.tasks == []

        mock_client_class.assert_called_once_with(
            "http://localhost:7700",
            "test_key",
            timeout=10,
            client_agents=("test-agent",),
        )

    @patch("meili._client._Client")
    def test_client_initialization_https(self, mock_client_class):
        https_settings = _MeiliSettings(
            host="example.com",
            port=443,
            https=True,
            master_key="https_key",
            timeout=30,
            sync=True,
            client_agents=None,
            debug=False,
            offline=False,
            batch_size=1000,
        )

        client = Client(https_settings)

        mock_client_class.assert_called_once_with(
            "https://example.com:443",
            "https_key",
            timeout=30,
            client_agents=None,
        )
        assert client.is_sync is True

    def test_flush_tasks(self):
        client = Client(self.settings)
        client.tasks = ["task1", "task2", "task3"]

        client.flush_tasks()

        assert client.tasks == []

    @patch("meili._client._Client")
    def test_with_settings_basic(self, mock_client_class):
        mock_client_instance = MagicMock()
        mock_index = MagicMock()
        mock_update_settings = MagicMock()

        mock_client_class.return_value = mock_client_instance
        mock_client_instance.index.return_value = mock_index
        mock_index.update_settings.return_value = mock_update_settings

        client = Client(self.settings)
        index_settings = MeiliIndexSettings()

        result = client.with_settings("test_index", index_settings)

        assert result == client

        mock_index.update_settings.assert_called_once()
        call_args = mock_index.update_settings.call_args[0][0]

        expected_settings = {
            "displayedAttributes": ["*"],
            "searchableAttributes": ["*"],
            "filterableAttributes": [],
            "sortableAttributes": [],
            "rankingRules": [
                "words",
                "typo",
                "proximity",
                "attribute",
                "sort",
                "exactness",
            ],
            "stopWords": [],
            "synonyms": {},
            "distinctAttribute": None,
            "typoTolerance": {
                "enabled": True,
                "minWordSizeForTypos": {"oneTypo": 5, "twoTypos": 9},
                "disableOnWords": [],
                "disableOnAttributes": [],
            },
            "faceting": {"maxValuesPerFacet": 100},
            "pagination": {"maxTotalHits": 1000},
        }

        assert call_args == expected_settings

    @patch("meili._client._Client")
    def test_with_settings_custom(self, mock_client_class):
        mock_client_instance = MagicMock()
        mock_index = MagicMock()
        mock_update_settings = MagicMock()

        mock_client_class.return_value = mock_client_instance
        mock_client_instance.index.return_value = mock_index
        mock_index.update_settings.return_value = mock_update_settings

        client = Client(self.settings)

        index_settings = MeiliIndexSettings(
            displayed_fields=["id", "title"],
            searchable_fields=["title", "content"],
            filterable_fields=["category", "status"],
            sortable_fields=["created_at", "updated_at"],
            ranking_rules=["words", "typo", "custom"],
            stop_words=["the", "and", "or"],
            synonyms={"car": ["automobile", "vehicle"]},
            distinct_attribute="id",
            typo_tolerance={"enabled": False},
            faceting={"maxValuesPerFacet": 50},
            pagination={"maxTotalHits": 500},
        )

        client.with_settings("custom_index", index_settings)

        call_args = mock_index.update_settings.call_args[0][0]

        assert call_args["displayedAttributes"] == ["id", "title"]
        assert call_args["searchableAttributes"] == ["title", "content"]
        assert call_args["filterableAttributes"] == ["category", "status"]
        assert call_args["sortableAttributes"] == ["created_at", "updated_at"]
        assert call_args["rankingRules"] == ["words", "typo", "custom"]
        assert call_args["stopWords"] == ["the", "and", "or"]
        assert call_args["synonyms"] == {"car": ["automobile", "vehicle"]}
        assert call_args["distinctAttribute"] == "id"
        assert call_args["typoTolerance"] == {"enabled": False}
        assert call_args["faceting"] == {"maxValuesPerFacet": 50}
        assert call_args["pagination"] == {"maxTotalHits": 500}

    @patch("meili._client._Client")
    def test_create_index_new(self, mock_client_class):
        mock_client_instance = MagicMock()
        mock_create_index = MagicMock()

        mock_client_class.return_value = mock_client_instance
        mock_client_instance.create_index.return_value = mock_create_index
        mock_client_instance.get_indexes.return_value = {"results": []}

        client = Client(self.settings)

        result = client.create_index("new_index", "id")

        assert result == client

        mock_client_instance.create_index.assert_called_once_with(
            "new_index", {"primaryKey": "id"}
        )
        assert len(client.tasks) == 1

    @patch("meili._client._Client")
    def test_create_index_existing(self, mock_client_class):
        mock_client_instance = MagicMock()
        existing_index = MagicMock()
        existing_index.uid = "existing_index"

        mock_client_class.return_value = mock_client_instance
        mock_client_instance.get_indexes.return_value = {
            "results": [existing_index]
        }

        client = Client(self.settings)

        result = client.create_index("existing_index", "id")

        assert result == client

        mock_client_instance.create_index.assert_not_called()
        assert len(client.tasks) == 0

    @patch("meili._client._Client")
    def test_get_index(self, mock_client_class):
        mock_client_instance = MagicMock()
        mock_index = MagicMock()

        mock_client_class.return_value = mock_client_instance
        mock_client_instance.index.return_value = mock_index

        client = Client(self.settings)

        result = client.get_index("test_index")

        assert result == mock_index
        mock_client_instance.index.assert_called_once_with("test_index")

    @patch("meili._client._Client")
    def test_wait_for_task(self, mock_client_class):
        mock_client_instance = MagicMock()
        mock_task = MagicMock()

        mock_client_class.return_value = mock_client_instance
        mock_client_instance.wait_for_task.return_value = mock_task

        client = Client(self.settings)

        result = client.wait_for_task(123)

        assert result == mock_task
        mock_client_instance.wait_for_task.assert_called_once_with(123)

    @patch("meili._client._Client")
    def test_get_indexes(self, mock_client_class):
        mock_client_instance = MagicMock()
        mock_indexes = [{"uid": "index1"}, {"uid": "index2"}]

        mock_client_class.return_value = mock_client_instance
        mock_client_instance.get_indexes.return_value = {
            "results": mock_indexes
        }

        client = Client(self.settings)

        result = client.get_indexes()

        assert result == mock_indexes
        mock_client_instance.get_indexes.assert_called_once()

    @patch("meili._client._Client")
    def test_update_display_attributes(self, mock_client_class):
        mock_client_instance = MagicMock()
        mock_index = MagicMock()
        mock_update = MagicMock()

        mock_client_class.return_value = mock_client_instance
        mock_client_instance.index.return_value = mock_index
        mock_index.update_displayed_attributes.return_value = mock_update

        client = Client(self.settings)

        result = client.update_display("test_index", ["id", "title"])

        assert result == client
        mock_index.update_displayed_attributes.assert_called_once_with(
            ["id", "title"]
        )

    @patch("meili._client._Client")
    def test_update_display_attributes_none(self, mock_client_class):
        mock_client_instance = MagicMock()
        mock_client_class.return_value = mock_client_instance

        client = Client(self.settings)

        result = client.update_display("test_index", None)

        assert result == client

    @patch("meili._client._Client")
    def test_update_searchable_attributes(self, mock_client_class):
        mock_client_instance = MagicMock()
        mock_index = MagicMock()
        mock_update = MagicMock()

        mock_client_class.return_value = mock_client_instance
        mock_client_instance.index.return_value = mock_index
        mock_index.update_searchable_attributes.return_value = mock_update

        client = Client(self.settings)

        result = client.update_searchable("test_index", ["title", "content"])

        assert result == client
        mock_index.update_searchable_attributes.assert_called_once_with(
            ["title", "content"]
        )

    @patch("meili._client._Client")
    def test_update_filterable_attributes(self, mock_client_class):
        mock_client_instance = MagicMock()
        mock_index = MagicMock()
        mock_update = MagicMock()

        mock_client_class.return_value = mock_client_instance
        mock_client_instance.index.return_value = mock_index
        mock_index.update_filterable_attributes.return_value = mock_update

        client = Client(self.settings)

        result = client.update_filterable("test_index", ["category", "status"])

        assert result == client
        mock_index.update_filterable_attributes.assert_called_once_with(
            ["category", "status"]
        )

    @patch("meili._client._Client")
    def test_update_sortable_attributes(self, mock_client_class):
        mock_client_instance = MagicMock()
        mock_index = MagicMock()
        mock_update = MagicMock()

        mock_client_class.return_value = mock_client_instance
        mock_client_instance.index.return_value = mock_index
        mock_index.update_sortable_attributes.return_value = mock_update

        client = Client(self.settings)

        result = client.update_sortable(
            "test_index", ["created_at", "updated_at"]
        )

        assert result == client
        mock_index.update_sortable_attributes.assert_called_once_with(
            ["created_at", "updated_at"]
        )

    @patch("meili._client._Client")
    def test_handle_sync_task_uid(self, mock_client_class):
        mock_client_instance = MagicMock()
        mock_task = MagicMock()
        mock_waited_task = MagicMock()

        mock_task.task_uid = 123
        mock_waited_task.status = "succeeded"

        mock_client_class.return_value = mock_client_instance
        mock_client_instance.wait_for_task.return_value = mock_waited_task

        sync_settings = _MeiliSettings(
            host="localhost",
            port=7700,
            https=False,
            master_key="test_key",
            sync=True,
            timeout=None,
            client_agents=None,
            debug=False,
            offline=False,
            batch_size=1000,
        )

        client = Client(sync_settings)

        result = client._handle_sync(mock_task)

        assert result == mock_waited_task
        mock_client_instance.wait_for_task.assert_called_once_with(123)

    @patch("meili._client._Client")
    def test_handle_sync_uid(self, mock_client_class):
        mock_client_instance = MagicMock()
        mock_task = MagicMock()
        mock_waited_task = MagicMock()

        del mock_task.task_uid
        mock_task.uid = 456
        mock_waited_task.status = "succeeded"

        mock_client_class.return_value = mock_client_instance
        mock_client_instance.wait_for_task.return_value = mock_waited_task

        sync_settings = _MeiliSettings(
            host="localhost",
            port=7700,
            https=False,
            master_key="test_key",
            sync=True,
            timeout=None,
            client_agents=None,
            debug=False,
            offline=False,
            batch_size=1000,
        )

        client = Client(sync_settings)

        with patch("builtins.hasattr") as mock_hasattr:

            def hasattr_side_effect(obj, attr):
                if attr == "task_uid":
                    return False
                elif attr == "uid":
                    return True
                return False

            mock_hasattr.side_effect = hasattr_side_effect

            result = client._handle_sync(mock_task)

        assert result == mock_waited_task
        mock_client_instance.wait_for_task.assert_called_once_with(456)

    @patch("meili._client._Client")
    def test_handle_sync_no_uid(self, mock_client_class):
        mock_client_instance = MagicMock()
        mock_task = MagicMock()

        mock_client_class.return_value = mock_client_instance

        sync_settings = _MeiliSettings(
            host="localhost",
            port=7700,
            https=False,
            master_key="test_key",
            sync=True,
            timeout=None,
            client_agents=None,
            debug=False,
            offline=False,
            batch_size=1000,
        )

        client = Client(sync_settings)

        with patch("builtins.hasattr") as mock_hasattr:
            mock_hasattr.return_value = False

            with pytest.raises(
                AttributeError, match="Task object has no uid attribute"
            ):
                client._handle_sync(mock_task)

    @patch("meili._client._Client")
    def test_handle_sync_failed_task(self, mock_client_class):
        mock_client_instance = MagicMock()
        mock_task = MagicMock()
        mock_waited_task = MagicMock()

        mock_task.task_uid = 789
        mock_waited_task.status = "failed"
        mock_waited_task.error = "Test error message"

        mock_client_class.return_value = mock_client_instance
        mock_client_instance.wait_for_task.return_value = mock_waited_task

        sync_settings = _MeiliSettings(
            host="localhost",
            port=7700,
            https=False,
            master_key="test_key",
            sync=True,
            timeout=None,
            client_agents=None,
            debug=False,
            offline=False,
            batch_size=1000,
        )

        client = Client(sync_settings)

        with pytest.raises(Exception, match="Test error message"):
            client._handle_sync(mock_task)

    @patch("meili._client._Client")
    def test_handle_sync_async_mode(self, mock_client_class):
        mock_client_instance = MagicMock()
        mock_task = MagicMock()

        mock_client_class.return_value = mock_client_instance

        client = Client(self.settings)

        result = client._handle_sync(mock_task)

        assert result == mock_task
        mock_client_instance.wait_for_task.assert_not_called()

    @patch("meili._settings._MeiliSettings.from_settings")
    @patch("meili._client._Client")
    def test_global_client_instance(
        self, mock_client_class, mock_from_settings
    ):
        assert isinstance(global_client, Client)
