"""
Unit tests for Meilisearch management commands.

This module tests the management commands created for the meilisearch-enhancements
feature, including argument validation, error messages, and output formatting.

Commands tested:
- meilisearch_enable_experimental
- meilisearch_update_index_settings
- meilisearch_update_ranking
- meilisearch_test_federated
- meilisearch_export_analytics
"""

import json
from datetime import timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


# Mock _meilisearch attribute for models
MOCK_PRODUCT_MEILISEARCH = {"index_name": "ProductTranslation"}
MOCK_BLOG_MEILISEARCH = {"index_name": "BlogPostTranslation"}


def create_mock_model(meilisearch_config):
    """Create a mock model with _meilisearch attribute."""
    mock = MagicMock()
    mock._meilisearch = meilisearch_config
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


class TestMeilisearchTestFederatedCommand:
    """Tests for meilisearch_test_federated management command."""

    @pytest.mark.parametrize(
        "query,language_code",
        [
            ("laptop", "en"),
            ("υπολογιστής", "el"),
            ("handy", "de"),
            ("test query", None),
        ],
    )
    @patch(
        "meili.management.commands.meilisearch_test_federated.BlogPostTranslation"
    )
    @patch(
        "meili.management.commands.meilisearch_test_federated.ProductTranslation"
    )
    @patch("meili.management.commands.meilisearch_test_federated.meili_client")
    def test_test_federated_valid_queries(
        self,
        mock_meili_client,
        mock_product,
        mock_blog,
        query,
        language_code,
    ):
        """Test federated search command with valid queries."""
        mock_product._meilisearch = MOCK_PRODUCT_MEILISEARCH
        mock_blog._meilisearch = MOCK_BLOG_MEILISEARCH

        mock_meili_client.client.multi_search.return_value = {
            "hits": [],
            "estimatedTotalHits": 0,
            "processingTimeMs": 10,
        }

        out = StringIO()
        args = ["meilisearch_test_federated", "--query", query]
        if language_code:
            args.extend(["--language-code", language_code])

        call_command(*args, stdout=out)

        output = out.getvalue()
        assert "FEDERATED SEARCH TEST" in output
        assert f"Query: {query}" in output
        assert "Search completed successfully" in output or "✓" in output

    @patch(
        "meili.management.commands.meilisearch_test_federated.BlogPostTranslation"
    )
    @patch(
        "meili.management.commands.meilisearch_test_federated.ProductTranslation"
    )
    @patch("meili.management.commands.meilisearch_test_federated.meili_client")
    def test_test_federated_with_limit(
        self, mock_meili_client, mock_product, mock_blog
    ):
        """Test federated search command with custom limit."""
        mock_product._meilisearch = MOCK_PRODUCT_MEILISEARCH
        mock_blog._meilisearch = MOCK_BLOG_MEILISEARCH

        mock_meili_client.client.multi_search.return_value = {
            "hits": [],
            "estimatedTotalHits": 0,
            "processingTimeMs": 10,
        }

        out = StringIO()
        call_command(
            "meilisearch_test_federated",
            "--query",
            "test",
            "--limit",
            "20",
            stdout=out,
        )

        output = out.getvalue()
        assert "Limit: 20" in output

    @patch(
        "meili.management.commands.meilisearch_test_federated.BlogPostTranslation"
    )
    @patch(
        "meili.management.commands.meilisearch_test_federated.ProductTranslation"
    )
    @patch("meili.management.commands.meilisearch_test_federated.meili_client")
    def test_test_federated_result_allocation_display(
        self, mock_meili_client, mock_product, mock_blog
    ):
        """Test that result allocation (70/30) is displayed."""
        mock_product._meilisearch = MOCK_PRODUCT_MEILISEARCH
        mock_blog._meilisearch = MOCK_BLOG_MEILISEARCH

        mock_meili_client.client.multi_search.return_value = {
            "hits": [],
            "estimatedTotalHits": 0,
            "processingTimeMs": 10,
        }

        out = StringIO()
        call_command(
            "meilisearch_test_federated",
            "--query",
            "test",
            "--limit",
            "10",
            stdout=out,
        )

        output = out.getvalue()
        assert "Result allocation" in output
        assert "Products" in output
        assert "Blog posts" in output
        assert "70%" in output
        assert "30%" in output

    @patch(
        "meili.management.commands.meilisearch_test_federated.BlogPostTranslation"
    )
    @patch(
        "meili.management.commands.meilisearch_test_federated.ProductTranslation"
    )
    @patch("meili.management.commands.meilisearch_test_federated.meili_client")
    def test_test_federated_greeklish_expansion(
        self, mock_meili_client, mock_product, mock_blog
    ):
        """Test Greeklish expansion for Greek language queries."""
        mock_product._meilisearch = MOCK_PRODUCT_MEILISEARCH
        mock_blog._meilisearch = MOCK_BLOG_MEILISEARCH

        mock_meili_client.client.multi_search.return_value = {
            "hits": [],
            "estimatedTotalHits": 0,
            "processingTimeMs": 10,
        }

        out = StringIO()
        call_command(
            "meilisearch_test_federated",
            "--query",
            "laptop",
            "--language-code",
            "el",
            stdout=out,
        )

        output = out.getvalue()
        assert "Language: el" in output
        assert "Greeklish expanded" in output

    @patch(
        "meili.management.commands.meilisearch_test_federated.BlogPostTranslation"
    )
    @patch(
        "meili.management.commands.meilisearch_test_federated.ProductTranslation"
    )
    @patch("meili.management.commands.meilisearch_test_federated.meili_client")
    def test_test_federated_filters_display(
        self, mock_meili_client, mock_product, mock_blog
    ):
        """Test that content filters are displayed."""
        mock_product._meilisearch = MOCK_PRODUCT_MEILISEARCH
        mock_blog._meilisearch = MOCK_BLOG_MEILISEARCH

        mock_meili_client.client.multi_search.return_value = {
            "hits": [],
            "estimatedTotalHits": 0,
            "processingTimeMs": 10,
        }

        out = StringIO()
        call_command(
            "meilisearch_test_federated",
            "--query",
            "test",
            "--language-code",
            "en",
            stdout=out,
        )

        output = out.getvalue()
        assert "Filters" in output
        assert "active = true" in output
        assert "is_deleted = false" in output
        assert "is_published = true" in output

    @patch(
        "meili.management.commands.meilisearch_test_federated.BlogPostTranslation"
    )
    @patch(
        "meili.management.commands.meilisearch_test_federated.ProductTranslation"
    )
    @patch("meili.management.commands.meilisearch_test_federated.meili_client")
    def test_test_federated_with_results(
        self, mock_meili_client, mock_product, mock_blog
    ):
        """Test federated search command with actual results."""
        mock_product._meilisearch = MOCK_PRODUCT_MEILISEARCH
        mock_blog._meilisearch = MOCK_BLOG_MEILISEARCH

        mock_meili_client.client.multi_search.return_value = {
            "hits": [
                {
                    "id": "1",
                    "name": "Test Product",
                    "_federation": {
                        "indexUid": "ProductTranslation",
                        "queriesPosition": 0,
                        "weightedRankingScore": 0.95,
                    },
                    "_rankingScore": 0.95,
                },
                {
                    "id": "2",
                    "title": "Test Blog Post",
                    "_federation": {
                        "indexUid": "BlogPostTranslation",
                        "queriesPosition": 1,
                        "weightedRankingScore": 0.85,
                    },
                    "_rankingScore": 0.85,
                },
            ],
            "estimatedTotalHits": 2,
            "processingTimeMs": 15,
        }

        out = StringIO()
        call_command(
            "meilisearch_test_federated",
            "--query",
            "test",
            stdout=out,
        )

        output = out.getvalue()
        assert "Total hits: 2" in output
        assert "Processing time: 15ms" in output
        assert "DETAILED RESULTS" in output
        assert "Product" in output
        assert "Blog Post" in output

    @patch(
        "meili.management.commands.meilisearch_test_federated.BlogPostTranslation"
    )
    @patch(
        "meili.management.commands.meilisearch_test_federated.ProductTranslation"
    )
    @patch("meili.management.commands.meilisearch_test_federated.meili_client")
    def test_test_federated_no_results(
        self, mock_meili_client, mock_product, mock_blog
    ):
        """Test federated search command with no results."""
        mock_product._meilisearch = MOCK_PRODUCT_MEILISEARCH
        mock_blog._meilisearch = MOCK_BLOG_MEILISEARCH

        mock_meili_client.client.multi_search.return_value = {
            "hits": [],
            "estimatedTotalHits": 0,
            "processingTimeMs": 5,
        }

        out = StringIO()
        call_command(
            "meilisearch_test_federated",
            "--query",
            "nonexistent_query_xyz",
            stdout=out,
        )

        output = out.getvalue()
        assert "No results found" in output

    @patch(
        "meili.management.commands.meilisearch_test_federated.BlogPostTranslation"
    )
    @patch(
        "meili.management.commands.meilisearch_test_federated.ProductTranslation"
    )
    @patch("meili.management.commands.meilisearch_test_federated.meili_client")
    def test_test_federated_error_handling(
        self, mock_meili_client, mock_product, mock_blog
    ):
        """Test error handling when Meilisearch fails."""
        mock_product._meilisearch = MOCK_PRODUCT_MEILISEARCH
        mock_blog._meilisearch = MOCK_BLOG_MEILISEARCH

        mock_meili_client.client.multi_search.side_effect = Exception(
            "Connection failed"
        )

        out = StringIO()
        call_command(
            "meilisearch_test_federated",
            "--query",
            "test",
            stdout=out,
        )

        output = out.getvalue()
        assert "Federated search failed" in output or "✗" in output


class TestMeilisearchExportAnalyticsCommand:
    """Tests for meilisearch_export_analytics management command."""

    @pytest.mark.django_db
    def test_export_analytics_no_data(self):
        """Test export command when no analytics data exists."""
        out = StringIO()
        call_command(
            "meilisearch_export_analytics",
            "--output",
            "/tmp/test_analytics.json",
            stdout=out,
        )

        output = out.getvalue()
        assert (
            "No queries found" in output
            or "Total queries to export: 0" in output
        )

    @pytest.mark.django_db
    def test_export_analytics_with_data(self):
        """Test export command with analytics data."""
        from search.models import SearchQuery

        SearchQuery.objects.create(
            query="test query",
            language_code="en",
            content_type="product",
            results_count=10,
            estimated_total_hits=100,
            processing_time_ms=50,
        )

        out = StringIO()
        output_file = "/tmp/test_analytics_export.json"
        call_command(
            "meilisearch_export_analytics",
            "--output",
            output_file,
            stdout=out,
        )

        output = out.getvalue()
        assert "Successfully exported" in output or "✓" in output
        assert "1" in output

        with open(output_file) as f:
            data = json.load(f)
            assert "export_metadata" in data
            assert "queries" in data
            assert len(data["queries"]) == 1
            assert data["queries"][0]["query"] == "test query"

    @pytest.mark.parametrize(
        "invalid_date",
        ["invalid-date", "2024/01/01", "01-01-2024", "not-a-date"],
    )
    @pytest.mark.django_db
    def test_export_analytics_invalid_date_format(self, invalid_date):
        """Test error handling for invalid date formats."""
        out = StringIO()
        call_command(
            "meilisearch_export_analytics",
            "--start-date",
            invalid_date,
            "--output",
            "/tmp/test.json",
            stdout=out,
        )

        output = out.getvalue()
        assert "Invalid" in output or "ISO format" in output

    @pytest.mark.django_db
    def test_export_analytics_date_range_filtering(self):
        """Test export with date range filtering."""
        from django.utils import timezone

        from search.models import SearchQuery

        now = timezone.now()

        old_query = SearchQuery.objects.create(
            query="old query",
            language_code="en",
            content_type="product",
            results_count=5,
            estimated_total_hits=50,
        )
        SearchQuery.objects.filter(pk=old_query.pk).update(
            timestamp=now - timedelta(days=30)
        )

        SearchQuery.objects.create(
            query="recent query",
            language_code="en",
            content_type="product",
            results_count=10,
            estimated_total_hits=100,
        )

        out = StringIO()
        output_file = "/tmp/test_analytics_filtered.json"

        start_date = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        call_command(
            "meilisearch_export_analytics",
            "--start-date",
            start_date,
            "--output",
            output_file,
            stdout=out,
        )

        with open(output_file) as f:
            data = json.load(f)
            assert len(data["queries"]) == 1
            assert data["queries"][0]["query"] == "recent query"

    @pytest.mark.django_db
    def test_export_analytics_include_clicks(self):
        """Test export with click data included."""
        from search.models import SearchClick, SearchQuery

        query = SearchQuery.objects.create(
            query="test query",
            language_code="en",
            content_type="product",
            results_count=10,
            estimated_total_hits=100,
        )
        SearchClick.objects.create(
            search_query=query,
            result_id="product_1",
            position=0,
        )

        out = StringIO()
        output_file = "/tmp/test_analytics_clicks.json"
        call_command(
            "meilisearch_export_analytics",
            "--output",
            output_file,
            "--include-clicks",
            stdout=out,
        )

        with open(output_file) as f:
            data = json.load(f)
            assert data["export_metadata"]["include_clicks"] is True
            assert "clicks" in data["queries"][0]
            assert len(data["queries"][0]["clicks"]) == 1
            assert data["queries"][0]["clicks"][0]["result_id"] == "product_1"

    @pytest.mark.django_db
    def test_export_analytics_output_formatting(self):
        """Test output formatting with progress indicators."""
        from search.models import SearchQuery

        SearchQuery.objects.create(
            query="test",
            language_code="en",
            content_type="product",
            results_count=10,
            estimated_total_hits=100,
        )

        out = StringIO()
        call_command(
            "meilisearch_export_analytics",
            "--output",
            "/tmp/test_format.json",
            stdout=out,
        )

        output = out.getvalue()
        assert "SEARCH ANALYTICS EXPORT" in output
        assert "Output file:" in output
        assert "Total queries to export:" in output
        assert "Export summary" in output


class TestManagementCommandsIntegration:
    """Integration tests for management commands working together."""

    @pytest.mark.parametrize(
        "command,required_args",
        [
            ("meilisearch_enable_experimental", ["--feature"]),
            ("meilisearch_update_index_settings", ["--index"]),
            ("meilisearch_update_ranking", ["--index", "--rules"]),
            ("meilisearch_test_federated", ["--query"]),
        ],
    )
    def test_commands_require_arguments(self, command, required_args):
        """Test that commands require their mandatory arguments."""
        with pytest.raises((CommandError, SystemExit)):
            call_command(command)

    @pytest.mark.parametrize(
        "command",
        [
            "meilisearch_enable_experimental",
            "meilisearch_update_index_settings",
            "meilisearch_update_ranking",
            "meilisearch_test_federated",
            "meilisearch_export_analytics",
        ],
    )
    def test_commands_have_help_text(self, command):
        """Test that all commands have help text."""
        from django.core.management import get_commands, load_command_class

        app_name = get_commands()[command]
        cmd_class = load_command_class(app_name, command)

        assert cmd_class.help is not None
        assert len(cmd_class.help) > 0

    def test_commands_integrate_with_existing_meili_commands(self):
        """Test that new commands integrate with existing Meilisearch commands."""
        mock_product = create_mock_model(MOCK_PRODUCT_MEILISEARCH)
        mock_blog = create_mock_model(MOCK_BLOG_MEILISEARCH)
        mock_indexes = {
            "ProductTranslation": mock_product,
            "BlogPostTranslation": mock_blog,
        }

        settings_module = __import__(
            "meili.management.commands.meilisearch_update_index_settings",
            fromlist=["Command"],
        )
        ranking_module = __import__(
            "meili.management.commands.meilisearch_update_ranking",
            fromlist=["Command"],
        )

        with (
            patch(
                "meili.management.commands.meilisearch_update_index_settings.meili_client"
            ),
            patch(
                "meili.management.commands.meilisearch_update_ranking.meili_client"
            ),
            patch.object(
                settings_module.Command,
                "AVAILABLE_INDEXES",
                mock_indexes,
            ),
            patch.object(
                ranking_module.Command,
                "AVAILABLE_INDEXES",
                mock_indexes,
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

            call_command(
                "meilisearch_update_ranking",
                "--index",
                "ProductTranslation",
                "--rules",
                "words,typo,proximity,attribute,sort,stock:desc,exactness",
                stdout=out,
            )

            output = out.getvalue()
            assert output.count("Successfully") >= 2 or output.count("✓") >= 2


class TestCommandOutputStyling:
    """Tests for command output styling (green success, red error)."""

    @patch("meili.management.commands.meilisearch_enable_experimental.requests")
    @patch(
        "meili.management.commands.meilisearch_enable_experimental.meili_client"
    )
    def test_success_message_styling(self, mock_meili_client, mock_requests):
        """Test that success messages use green styling."""
        mock_settings = MagicMock()
        mock_settings.https = False
        mock_settings.host = "localhost"
        mock_settings.port = 7700
        mock_settings.master_key = "test_key"
        mock_settings.timeout = 10
        mock_meili_client.settings = mock_settings

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_requests.patch.return_value = mock_response
        mock_requests.get.return_value = mock_response
        mock_response.json.return_value = {"containsFilter": True}

        out = StringIO()
        call_command(
            "meilisearch_enable_experimental",
            "--feature",
            "containsFilter",
            stdout=out,
        )

        output = out.getvalue()
        assert "✓" in output or "Successfully" in output

    @patch(
        "meili.management.commands.meilisearch_enable_experimental.meili_client"
    )
    def test_error_message_styling(self, mock_meili_client):
        """Test that error messages use red styling."""
        mock_settings = MagicMock()
        mock_meili_client.settings = mock_settings

        out = StringIO()
        call_command(
            "meilisearch_enable_experimental",
            "--feature",
            "invalidFeature",
            stdout=out,
        )

        output = out.getvalue()
        assert "Unknown feature" in output or "Available features" in output


class TestCommandArgumentValidation:
    """
    For any management command with invalid arguments, the system should
    display helpful error messages with usage examples.
    """

    @pytest.mark.parametrize(
        "command,invalid_args,expected_error_patterns",
        [
            (
                "meilisearch_enable_experimental",
                {"feature": "nonexistent_feature"},
                ["Unknown feature", "Available features"],
            ),
            (
                "meilisearch_update_index_settings",
                {"index": "NonExistentIndex", "max_total_hits": "50000"},
                ["Unknown index", "Available indexes"],
            ),
            (
                "meilisearch_update_index_settings",
                {"index": "ProductTranslation"},
                ["At least one setting must be provided"],
            ),
            (
                "meilisearch_update_ranking",
                {"index": "InvalidIndex", "rules": "words,typo,exactness"},
                ["Unknown index", "Available indexes"],
            ),
            (
                "meilisearch_update_ranking",
                {"index": "ProductTranslation", "rules": "invalid_rule"},
                ["Unknown ranking rule", "Invalid"],
            ),
            (
                "meilisearch_update_ranking",
                {"index": "ProductTranslation", "rules": "stock:invalid"},
                ["Invalid sort direction", "asc", "desc"],
            ),
            (
                "meilisearch_update_ranking",
                {"index": "ProductTranslation", "rules": ""},
                ["No ranking rules provided"],
            ),
            (
                "meilisearch_update_ranking",
                {
                    "index": "ProductTranslation",
                    "rules": "field:asc:extra:parts",
                },
                ["Invalid custom rule format"],
            ),
        ],
    )
    @patch(
        "meili.management.commands.meilisearch_enable_experimental.meili_client"
    )
    def test_invalid_arguments_produce_helpful_error_messages(
        self,
        mock_experimental_client,
        command,
        invalid_args,
        expected_error_patterns,
    ):
        mock_settings = MagicMock()
        mock_settings.https = False
        mock_settings.host = "localhost"
        mock_settings.port = 7700
        mock_experimental_client.settings = mock_settings

        out = StringIO()

        cmd_args = [command]
        for key, value in invalid_args.items():
            cmd_args.extend([f"--{key.replace('_', '-')}", value])

        call_command(*cmd_args, stdout=out)

        output = out.getvalue()
        error_found = any(
            pattern in output for pattern in expected_error_patterns
        )
        assert error_found, (
            f"Expected one of {expected_error_patterns} in output, "
            f"but got: {output}"
        )

    @pytest.mark.parametrize(
        "command,missing_args",
        [
            ("meilisearch_enable_experimental", ["--feature"]),
            ("meilisearch_update_index_settings", ["--index"]),
            ("meilisearch_update_ranking", ["--index", "--rules"]),
            ("meilisearch_test_federated", ["--query"]),
        ],
    )
    def test_missing_required_arguments_raise_error(
        self, command, missing_args
    ):
        with pytest.raises((CommandError, SystemExit)):
            call_command(command)

    @pytest.mark.parametrize(
        "invalid_date",
        [
            "invalid-date",
            "2024/01/01",
            "01-01-2024",
            "not-a-date",
            "2024-13-01",
            "2024-01-32",
        ],
    )
    @pytest.mark.django_db
    def test_export_analytics_invalid_date_produces_helpful_error(
        self, invalid_date
    ):
        out = StringIO()
        call_command(
            "meilisearch_export_analytics",
            "--start-date",
            invalid_date,
            "--output",
            "/tmp/test.json",
            stdout=out,
        )

        output = out.getvalue()
        assert (
            "Invalid" in output
            or "ISO format" in output
            or "YYYY-MM-DD" in output
        )

    @pytest.mark.parametrize(
        "command",
        [
            "meilisearch_enable_experimental",
            "meilisearch_update_index_settings",
            "meilisearch_update_ranking",
            "meilisearch_test_federated",
            "meilisearch_export_analytics",
        ],
    )
    def test_commands_provide_help_with_usage_examples(self, command):
        from django.core.management import get_commands, load_command_class

        app_name = get_commands()[command]
        cmd_class = load_command_class(app_name, command)

        assert cmd_class.help is not None
        assert len(cmd_class.help) > 10

        parser = cmd_class.create_parser("manage.py", command)
        help_output = parser.format_help()

        assert command in help_output or "usage" in help_output.lower()
        assert "--" in help_output

    @pytest.mark.parametrize(
        "command,args,error_should_list_options",
        [
            (
                "meilisearch_enable_experimental",
                {"feature": "badFeature"},
                "Available features",
            ),
            (
                "meilisearch_update_index_settings",
                {"index": "BadIndex", "max_total_hits": "1000"},
                "Available indexes",
            ),
            (
                "meilisearch_update_ranking",
                {"index": "BadIndex", "rules": "words"},
                "Available indexes",
            ),
        ],
    )
    @patch(
        "meili.management.commands.meilisearch_enable_experimental.meili_client"
    )
    def test_error_messages_list_available_options(
        self, mock_client, command, args, error_should_list_options
    ):
        mock_settings = MagicMock()
        mock_settings.https = False
        mock_settings.host = "localhost"
        mock_settings.port = 7700
        mock_client.settings = mock_settings

        out = StringIO()

        cmd_args = [command]
        for key, value in args.items():
            cmd_args.extend([f"--{key.replace('_', '-')}", value])

        call_command(*cmd_args, stdout=out)

        output = out.getvalue()
        assert error_should_list_options in output, (
            f"Expected '{error_should_list_options}' in output, but got: {output}"
        )

    @pytest.mark.parametrize(
        "numeric_arg,invalid_value",
        [
            ("--max-total-hits", "not_a_number"),
            ("--search-cutoff-ms", "abc"),
            ("--max-values-per-facet", "xyz"),
            ("--limit", "not_numeric"),
        ],
    )
    def test_numeric_arguments_validate_input(self, numeric_arg, invalid_value):
        out = StringIO()

        if numeric_arg in [
            "--max-total-hits",
            "--search-cutoff-ms",
            "--max-values-per-facet",
        ]:
            command = "meilisearch_update_index_settings"
            cmd_args = [
                command,
                "--index",
                "ProductTranslation",
                numeric_arg,
                invalid_value,
            ]
        else:
            command = "meilisearch_test_federated"
            cmd_args = [command, "--query", "test", numeric_arg, invalid_value]

        try:
            call_command(*cmd_args, stdout=out, stderr=out)
            output = out.getvalue()
            assert "invalid" in output.lower() or "error" in output.lower()
        except SystemExit, CommandError, ValueError:
            pass  # Expected behavior - invalid numeric input should fail
