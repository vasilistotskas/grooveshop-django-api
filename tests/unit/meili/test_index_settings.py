"""
Property validation tests for MeiliIndexSettings configuration.

This module tests that MeiliIndexSettings dataclass correctly handles
searchCutoffMs and other configuration options, ensuring they are properly
included in the settings payload sent to Meilisearch.
"""

from unittest.mock import MagicMock, patch

import pytest

from meili._client import Client
from meili._settings import _MeiliSettings
from meili.dataclasses import MeiliIndexSettings


class TestMeiliIndexSettings:
    """Test suite for MeiliIndexSettings dataclass and settings application."""

    def setup_method(self):
        """Set up test fixtures."""
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

    @pytest.mark.parametrize(
        "search_cutoff_ms,expected_in_payload",
        [
            (500, True),
            (1000, True),
            (1500, True),
            (2000, True),
            (3000, True),
            (5000, True),
            (10000, True),
            (None, False),
        ],
    )
    @patch("meili._client._Client")
    def test_search_cutoff_ms_in_settings_payload(
        self, mock_client_class, search_cutoff_ms, expected_in_payload
    ):
        """
        For any MeiliIndexSettings with search_cutoff_ms value, the Client.with_settings
        method should include searchCutoffMs in the settings payload sent to Meilisearch.
        """
        mock_client_instance = MagicMock()
        mock_index = MagicMock()
        mock_update_settings = MagicMock()

        mock_client_class.return_value = mock_client_instance
        mock_client_instance.index.return_value = mock_index
        mock_index.update_settings.return_value = mock_update_settings

        client = Client(self.settings)
        index_settings = MeiliIndexSettings(search_cutoff_ms=search_cutoff_ms)

        client.with_settings("test_index", index_settings)

        call_args = mock_index.update_settings.call_args[0][0]

        if expected_in_payload:
            assert "searchCutoffMs" in call_args
            assert call_args["searchCutoffMs"] == search_cutoff_ms
        else:
            assert "searchCutoffMs" not in call_args

    @pytest.mark.parametrize(
        "settings_config,expected_payload_keys",
        [
            # Test with only search_cutoff_ms
            (
                {"search_cutoff_ms": 1500},
                ["searchCutoffMs"],
            ),
            # Test with search_cutoff_ms and pagination
            (
                {
                    "search_cutoff_ms": 2000,
                    "pagination": {"maxTotalHits": 50000},
                },
                ["searchCutoffMs", "pagination"],
            ),
            # Test with search_cutoff_ms and faceting
            (
                {
                    "search_cutoff_ms": 1500,
                    "faceting": {"maxValuesPerFacet": 100},
                },
                ["searchCutoffMs", "faceting"],
            ),
            # Test with all settings
            (
                {
                    "search_cutoff_ms": 1500,
                    "displayed_fields": ["id", "title"],
                    "searchable_fields": ["title", "content"],
                    "filterable_fields": ["category"],
                    "sortable_fields": ["created_at"],
                    "ranking_rules": ["words", "typo"],
                    "pagination": {"maxTotalHits": 50000},
                    "faceting": {"maxValuesPerFacet": 100},
                },
                [
                    "searchCutoffMs",
                    "displayedAttributes",
                    "searchableAttributes",
                    "filterableAttributes",
                    "sortableAttributes",
                    "rankingRules",
                    "pagination",
                    "faceting",
                ],
            ),
        ],
    )
    @patch("meili._client._Client")
    def test_search_cutoff_ms_with_other_settings(
        self, mock_client_class, settings_config, expected_payload_keys
    ):
        """
        Test that searchCutoffMs is correctly included alongside other settings.

        Validates: Requirements 4.2
        """
        mock_client_instance = MagicMock()
        mock_index = MagicMock()
        mock_update_settings = MagicMock()

        mock_client_class.return_value = mock_client_instance
        mock_client_instance.index.return_value = mock_index
        mock_index.update_settings.return_value = mock_update_settings

        client = Client(self.settings)
        index_settings = MeiliIndexSettings(**settings_config)

        client.with_settings("test_index", index_settings)

        call_args = mock_index.update_settings.call_args[0][0]

        for key in expected_payload_keys:
            assert key in call_args, (
                f"Expected key '{key}' not found in payload"
            )

        if "searchCutoffMs" in expected_payload_keys:
            assert (
                call_args["searchCutoffMs"]
                == settings_config["search_cutoff_ms"]
            )

    @patch("meili._client._Client")
    def test_meili_index_settings_dataclass_defaults(self, mock_client_class):
        """
        Test that MeiliIndexSettings dataclass has correct default values.

        Validates: Requirements 4.1
        """
        index_settings = MeiliIndexSettings()

        assert index_settings.displayed_fields is None
        assert index_settings.searchable_fields is None
        assert index_settings.filterable_fields is None
        assert index_settings.sortable_fields is None
        assert index_settings.ranking_rules is None
        assert index_settings.stop_words is None
        assert index_settings.synonyms is None
        assert index_settings.distinct_attribute is None
        assert index_settings.typo_tolerance is None
        assert index_settings.faceting is None
        assert index_settings.pagination is None
        assert index_settings.search_cutoff_ms is None

    @pytest.mark.parametrize(
        "search_cutoff_ms",
        [0, 1, 100, 500, 1000, 1500, 2000, 5000, 10000, 30000],
    )
    def test_meili_index_settings_search_cutoff_ms_values(
        self, search_cutoff_ms
    ):
        """
        Test that MeiliIndexSettings accepts various search_cutoff_ms values.

        Validates: Requirements 4.1
        """
        index_settings = MeiliIndexSettings(search_cutoff_ms=search_cutoff_ms)

        assert index_settings.search_cutoff_ms == search_cutoff_ms

    @patch("meili._client._Client")
    def test_settings_update_without_reindexing(self, mock_client_class):
        """
        Test that update_meili_settings updates configuration without reindexing documents.
        This is a placeholder test - actual implementation will be tested with real models.
        """
        mock_client_instance = MagicMock()
        mock_index = MagicMock()
        mock_update_settings = MagicMock()

        mock_client_class.return_value = mock_client_instance
        mock_client_instance.index.return_value = mock_index
        mock_index.update_settings.return_value = mock_update_settings

        client = Client(self.settings)
        index_settings = MeiliIndexSettings(
            search_cutoff_ms=1500,
            pagination={"maxTotalHits": 50000},
        )

        client.with_settings("test_index", index_settings)

        # Verify update_settings was called (not add_documents or update_documents)
        mock_index.update_settings.assert_called_once()
        mock_index.add_documents.assert_not_called()
        mock_index.update_documents.assert_not_called()

    @pytest.mark.parametrize(
        "invalid_value",
        ["1500", "invalid", [], {}, -1, -100],
    )
    def test_meili_index_settings_invalid_search_cutoff_ms(self, invalid_value):
        """
        Test that MeiliIndexSettings handles invalid search_cutoff_ms values.

        Note: Python dataclasses don't enforce type checking at runtime by default,
        but this test documents expected behavior.

        Validates: Requirements 4.1
        """
        # This will not raise an error in Python dataclasses without additional validation
        # but documents the expected type
        index_settings = MeiliIndexSettings(search_cutoff_ms=invalid_value)

        # The value is stored as-is (Python doesn't enforce types at runtime)
        assert index_settings.search_cutoff_ms == invalid_value
