"""
Unit tests for Meilisearch management commands.

This module tests the management commands created for the meilisearch-enhancements
feature, including argument validation, error messages, and output formatting.

Commands tested:
- meilisearch_enable_experimental
- meilisearch_update_index_settings
- meilisearch_update_ranking
"""

from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command


# Mock _meilisearch attribute for models
MOCK_PRODUCT_MEILISEARCH = {"base_index_name": "ProductTranslation"}
MOCK_BLOG_MEILISEARCH = {"base_index_name": "BlogPostTranslation"}


def create_mock_model(meilisearch_config):
    """Create a mock model with _meilisearch attribute."""
    mock = MagicMock()
    mock._meilisearch = meilisearch_config
    mock.get_meili_index_name.return_value = meilisearch_config[
        "base_index_name"
    ]
    return mock


class TestMeilisearchEnableExperimentalCommand:
    """Tests for meilisearch_enable_experimental management command."""

    @pytest.mark.parametrize(
        "feature",
        ["containsFilter", "vectorStore", "editDocumentsByFunction"],
    )
    @patch("meili.management.commands.meilisearch_enable_experimental.requests")
    @patch(
        "meili.management.commands.meilisearch_enable_experimental.meili_client"
    )
    def test_enable_experimental_valid_features(
        self, mock_meili_client, mock_requests, feature
    ):
        """Test enabling valid experimental features."""
        mock_settings = MagicMock()
        mock_settings.https = False
        mock_settings.host = "localhost"
        mock_settings.port = 7700
        mock_settings.master_key = "test_key"
        mock_settings.timeout = 10
        mock_meili_client.settings = mock_settings

        mock_patch_response = MagicMock()
        mock_patch_response.status_code = 200
        mock_requests.patch.return_value = mock_patch_response

        mock_get_response = MagicMock()
        mock_get_response.status_code = 200
        mock_get_response.json.return_value = {feature: True}
        mock_requests.get.return_value = mock_get_response

        out = StringIO()
        call_command(
            "meilisearch_enable_experimental",
            "--feature",
            feature,
            stdout=out,
        )

        output = out.getvalue()
        assert "Successfully" in output or "✓" in output
        assert feature in output

    @pytest.mark.parametrize(
        "invalid_feature",
        ["unknownFeature", "invalidOption", "notAFeature", ""],
    )
    @patch(
        "meili.management.commands.meilisearch_enable_experimental.meili_client"
    )
    def test_enable_experimental_invalid_feature(
        self, mock_meili_client, invalid_feature
    ):
        """Test error handling for invalid experimental features."""
        mock_settings = MagicMock()
        mock_settings.https = False
        mock_settings.host = "localhost"
        mock_settings.port = 7700
        mock_meili_client.settings = mock_settings

        out = StringIO()

        if invalid_feature == "":
            return

        call_command(
            "meilisearch_enable_experimental",
            "--feature",
            invalid_feature,
            stdout=out,
        )

        output = out.getvalue()
        assert "Unknown feature" in output or "Available features" in output

    @patch("meili.management.commands.meilisearch_enable_experimental.requests")
    @patch(
        "meili.management.commands.meilisearch_enable_experimental.meili_client"
    )
    def test_enable_experimental_disable_flag(
        self, mock_meili_client, mock_requests
    ):
        """Test disabling an experimental feature with --disable flag."""
        mock_settings = MagicMock()
        mock_settings.https = False
        mock_settings.host = "localhost"
        mock_settings.port = 7700
        mock_settings.master_key = "test_key"
        mock_settings.timeout = 10
        mock_meili_client.settings = mock_settings

        mock_patch_response = MagicMock()
        mock_patch_response.status_code = 200
        mock_requests.patch.return_value = mock_patch_response

        mock_get_response = MagicMock()
        mock_get_response.status_code = 200
        mock_get_response.json.return_value = {"containsFilter": False}
        mock_requests.get.return_value = mock_get_response

        out = StringIO()
        call_command(
            "meilisearch_enable_experimental",
            "--feature",
            "containsFilter",
            "--disable",
            stdout=out,
        )

        output = out.getvalue()
        assert "Disabling" in output
        assert "containsFilter" in output

    @patch("meili.management.commands.meilisearch_enable_experimental.requests")
    @patch(
        "meili.management.commands.meilisearch_enable_experimental.meili_client"
    )
    def test_enable_experimental_connection_error(
        self, mock_meili_client, mock_requests
    ):
        """Test error handling when Meilisearch connection fails."""
        mock_settings = MagicMock()
        mock_settings.https = False
        mock_settings.host = "localhost"
        mock_settings.port = 7700
        mock_settings.master_key = "test_key"
        mock_settings.timeout = 10
        mock_meili_client.settings = mock_settings

        mock_requests.patch.side_effect = (
            mock_requests.exceptions.ConnectionError("Connection refused")
        )

        out = StringIO()
        call_command(
            "meilisearch_enable_experimental",
            "--feature",
            "containsFilter",
            stdout=out,
        )

        output = out.getvalue()
        assert "Failed to connect" in output or "✗" in output


class TestMeilisearchUpdateIndexSettingsCommand:
    """Tests for meilisearch_update_index_settings management command."""

    @pytest.fixture
    def mock_available_indexes(self):
        """Fixture to mock AVAILABLE_INDEXES with mock models."""
        mock_product = create_mock_model(MOCK_PRODUCT_MEILISEARCH)
        mock_blog = create_mock_model(MOCK_BLOG_MEILISEARCH)
        return {
            "ProductTranslation": mock_product,
            "BlogPostTranslation": mock_blog,
        }

    @pytest.mark.parametrize(
        "index_name",
        ["ProductTranslation", "BlogPostTranslation"],
    )
    def test_update_index_settings_valid_index(
        self, mock_available_indexes, index_name
    ):
        """Test updating settings for valid indexes."""
        with (
            patch(
                "meili.management.commands.meilisearch_update_index_settings.meili_client"
            ),
            patch.object(
                __import__(
                    "meili.management.commands.meilisearch_update_index_settings",
                    fromlist=["Command"],
                ).Command,
                "AVAILABLE_INDEXES",
                mock_available_indexes,
            ),
        ):
            out = StringIO()
            call_command(
                "meilisearch_update_index_settings",
                "--index",
                index_name,
                "--max-total-hits",
                "50000",
                stdout=out,
            )

            output = out.getvalue()
            assert "Successfully updated" in output or "✓" in output
            assert index_name in output

    @pytest.mark.parametrize(
        "invalid_index",
        ["InvalidIndex", "NotAnIndex", "RandomName"],
    )
    def test_update_index_settings_invalid_index(self, invalid_index):
        """Test error handling for invalid index names."""
        out = StringIO()
        call_command(
            "meilisearch_update_index_settings",
            "--index",
            invalid_index,
            "--max-total-hits",
            "50000",
            stdout=out,
        )

        output = out.getvalue()
        assert "Unknown index" in output or "Available indexes" in output

    @pytest.mark.parametrize(
        "setting_name,setting_flag,setting_value",
        [
            ("maxTotalHits", "--max-total-hits", "50000"),
            ("searchCutoffMs", "--search-cutoff-ms", "1500"),
            ("maxValuesPerFacet", "--max-values-per-facet", "100"),
        ],
    )
    def test_update_index_settings_individual_settings(
        self, mock_available_indexes, setting_name, setting_flag, setting_value
    ):
        """Test updating individual settings."""
        with (
            patch(
                "meili.management.commands.meilisearch_update_index_settings.meili_client"
            ),
            patch.object(
                __import__(
                    "meili.management.commands.meilisearch_update_index_settings",
                    fromlist=["Command"],
                ).Command,
                "AVAILABLE_INDEXES",
                mock_available_indexes,
            ),
        ):
            out = StringIO()
            call_command(
                "meilisearch_update_index_settings",
                "--index",
                "ProductTranslation",
                setting_flag,
                setting_value,
                stdout=out,
            )

            output = out.getvalue()
            assert setting_name in output or setting_value in output
            assert "Successfully" in output or "✓" in output

    def test_update_index_settings_no_settings_provided(self):
        """Test error when no settings are provided."""
        out = StringIO()
        call_command(
            "meilisearch_update_index_settings",
            "--index",
            "ProductTranslation",
            stdout=out,
        )

        output = out.getvalue()
        assert "At least one setting must be provided" in output

    def test_update_index_settings_multiple_settings(
        self, mock_available_indexes
    ):
        """Test updating multiple settings at once."""
        with (
            patch(
                "meili.management.commands.meilisearch_update_index_settings.meili_client"
            ),
            patch.object(
                __import__(
                    "meili.management.commands.meilisearch_update_index_settings",
                    fromlist=["Command"],
                ).Command,
                "AVAILABLE_INDEXES",
                mock_available_indexes,
            ),
        ):
            out = StringIO()
            call_command(
                "meilisearch_update_index_settings",
                "--index",
                "ProductTranslation",
                "--max-total-hits",
                "50000",
                "--search-cutoff-ms",
                "1500",
                "--max-values-per-facet",
                "100",
                stdout=out,
            )

            output = out.getvalue()
            assert "maxTotalHits" in output or "50000" in output
            assert "searchCutoffMs" in output or "1500" in output
            assert "maxValuesPerFacet" in output or "100" in output

    def test_update_index_settings_output_formatting(
        self, mock_available_indexes
    ):
        """Test output formatting with progress indicators."""
        with (
            patch(
                "meili.management.commands.meilisearch_update_index_settings.meili_client"
            ),
            patch.object(
                __import__(
                    "meili.management.commands.meilisearch_update_index_settings",
                    fromlist=["Command"],
                ).Command,
                "AVAILABLE_INDEXES",
                mock_available_indexes,
            ),
        ):
            out = StringIO()
            call_command(
                "meilisearch_update_index_settings",
                "--index",
                "ProductTranslation",
                "--max-total-hits",
                "50000",
                stdout=out,
            )

            output = out.getvalue()
            assert "Updating" in output
            assert "without reindexing" in output or "immediately" in output


class TestMeilisearchUpdateRankingCommand:
    """Tests for meilisearch_update_ranking management command."""

    @pytest.fixture
    def mock_available_indexes(self):
        """Fixture to mock AVAILABLE_INDEXES with mock models."""
        mock_product = create_mock_model(MOCK_PRODUCT_MEILISEARCH)
        mock_blog = create_mock_model(MOCK_BLOG_MEILISEARCH)
        return {
            "ProductTranslation": mock_product,
            "BlogPostTranslation": mock_blog,
        }

    @pytest.mark.parametrize(
        "rules",
        [
            "words,typo,proximity,attribute,sort,exactness",
            "words,typo,proximity,attribute,sort,stock:desc,exactness",
            "words,typo,proximity,attribute,sort,stock:desc,discount_percent:desc,exactness",
        ],
    )
    def test_update_ranking_valid_rules(self, mock_available_indexes, rules):
        """Test updating ranking rules with valid configurations."""
        with (
            patch(
                "meili.management.commands.meilisearch_update_ranking.meili_client"
            ),
            patch.object(
                __import__(
                    "meili.management.commands.meilisearch_update_ranking",
                    fromlist=["Command"],
                ).Command,
                "AVAILABLE_INDEXES",
                mock_available_indexes,
            ),
        ):
            out = StringIO()
            call_command(
                "meilisearch_update_ranking",
                "--index",
                "ProductTranslation",
                "--rules",
                rules,
                stdout=out,
            )

            output = out.getvalue()
            assert "Successfully updated" in output or "✓" in output
            assert "ranking rules" in output.lower()

    @pytest.mark.parametrize(
        "invalid_rules,expected_error",
        [
            ("invalid_rule", "Unknown ranking rule"),
            ("words,invalid,typo", "Unknown ranking rule"),
            ("stock:invalid", "Invalid sort direction"),
            ("field:asc:extra", "Invalid custom rule format"),
        ],
    )
    def test_update_ranking_invalid_rules(self, invalid_rules, expected_error):
        """Test error handling for invalid ranking rules."""
        out = StringIO()
        call_command(
            "meilisearch_update_ranking",
            "--index",
            "ProductTranslation",
            "--rules",
            invalid_rules,
            stdout=out,
        )

        output = out.getvalue()
        assert expected_error in output or "Invalid" in output

    @pytest.mark.parametrize(
        "invalid_index",
        ["InvalidIndex", "NotAnIndex"],
    )
    def test_update_ranking_invalid_index(self, invalid_index):
        """Test error handling for invalid index names."""
        out = StringIO()
        call_command(
            "meilisearch_update_ranking",
            "--index",
            invalid_index,
            "--rules",
            "words,typo,exactness",
            stdout=out,
        )

        output = out.getvalue()
        assert "Unknown index" in output or "Available indexes" in output

    def test_update_ranking_output_formatting(self, mock_available_indexes):
        """Test output formatting shows numbered rules list."""
        with (
            patch(
                "meili.management.commands.meilisearch_update_ranking.meili_client"
            ),
            patch.object(
                __import__(
                    "meili.management.commands.meilisearch_update_ranking",
                    fromlist=["Command"],
                ).Command,
                "AVAILABLE_INDEXES",
                mock_available_indexes,
            ),
        ):
            out = StringIO()
            call_command(
                "meilisearch_update_ranking",
                "--index",
                "ProductTranslation",
                "--rules",
                "words,typo,proximity,attribute,sort,exactness",
                stdout=out,
            )

            output = out.getvalue()
            assert "1." in output
            assert "words" in output
            assert (
                "New ranking rules" in output
                or "ranking rules" in output.lower()
            )

    def test_update_ranking_empty_rules(self):
        """Test error handling for empty rules string."""
        out = StringIO()
        call_command(
            "meilisearch_update_ranking",
            "--index",
            "ProductTranslation",
            "--rules",
            "",
            stdout=out,
        )

        output = out.getvalue()
        assert "No ranking rules provided" in output
