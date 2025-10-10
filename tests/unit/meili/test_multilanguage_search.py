"""Tests for multilanguage search functionality with Meilisearch."""

from unittest.mock import MagicMock, patch


from meili.querysets import IndexQuerySet


class MockTranslationModel:
    """Mock model for translation testing."""

    __name__ = "MockTranslationModel"
    _meilisearch = {
        "index_name": "test_translation_index",
        "supports_geo": False,
    }

    class MeiliMeta:
        primary_key = "id"

    objects = MagicMock()


class TestMultilanguageSearch:
    """Test suite for multilanguage search functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_index = MagicMock()
        self.mock_client = MagicMock()
        self.mock_client.get_index.return_value = self.mock_index

    @patch("meili.querysets.client")
    def test_search_with_language_filter(self, mock_client):
        """Test search with language_code filter."""
        mock_client.get_index.return_value = self.mock_index

        mock_search_results = {
            "hits": [
                {
                    "id": 1,
                    "title": "English Post",
                    "language_code": "en",
                },
                {
                    "id": 2,
                    "title": "Greek Post",
                    "language_code": "el",
                },
            ],
            "estimatedTotalHits": 2,
        }
        self.mock_index.search.return_value = mock_search_results

        mock_obj1 = MagicMock()
        mock_obj1.pk = 1
        mock_obj2 = MagicMock()
        mock_obj2.pk = 2

        mock_queryset = MagicMock()
        mock_queryset.order_by.return_value = [mock_obj1, mock_obj2]
        MockTranslationModel.objects.filter.return_value = mock_queryset

        queryset = IndexQuerySet(MockTranslationModel)
        queryset.filter(language_code="en")
        queryset.search("test query")

        call_args = self.mock_index.search.call_args[0][1]
        assert "language_code = 'en'" in call_args["filter"]

    @patch("meili.querysets.client")
    def test_search_with_locale_setting(self, mock_client):
        """Test search with locale configuration."""
        mock_client.get_index.return_value = self.mock_index

        mock_search_results = {
            "hits": [{"id": 1, "title": "Ελληνικό Κείμενο"}],
            "estimatedTotalHits": 1,
        }
        self.mock_index.search.return_value = mock_search_results

        mock_obj = MagicMock()
        mock_obj.pk = 1

        mock_queryset = MagicMock()
        mock_queryset.order_by.return_value = [mock_obj]
        MockTranslationModel.objects.filter.return_value = mock_queryset

        queryset = IndexQuerySet(MockTranslationModel)
        queryset.locales("el")
        queryset.search("κείμενο")

        call_args = self.mock_index.search.call_args[0][1]
        assert call_args["locales"] == ["el"]

    @patch("meili.querysets.client")
    def test_search_with_multiple_locales(self, mock_client):
        """Test search with multiple locales."""
        mock_client.get_index.return_value = self.mock_index

        mock_search_results = {"hits": [], "estimatedTotalHits": 0}
        self.mock_index.search.return_value = mock_search_results

        mock_queryset = MagicMock()
        mock_queryset.order_by.return_value = []
        MockTranslationModel.objects.filter.return_value = mock_queryset

        queryset = IndexQuerySet(MockTranslationModel)
        queryset.locales("en", "el", "de")
        queryset.search("test")

        call_args = self.mock_index.search.call_args[0][1]
        assert call_args["locales"] == ["en", "el", "de"]

    @patch("meili.querysets.client")
    def test_search_without_language_filter(self, mock_client):
        """Test search without language filter returns all languages."""
        mock_client.get_index.return_value = self.mock_index

        mock_search_results = {
            "hits": [
                {"id": 1, "language_code": "en"},
                {"id": 2, "language_code": "el"},
                {"id": 3, "language_code": "de"},
            ],
            "estimatedTotalHits": 3,
        }
        self.mock_index.search.return_value = mock_search_results

        mock_obj1 = MagicMock()
        mock_obj1.pk = 1
        mock_obj2 = MagicMock()
        mock_obj2.pk = 2
        mock_obj3 = MagicMock()
        mock_obj3.pk = 3

        mock_queryset = MagicMock()
        mock_queryset.order_by.return_value = [mock_obj1, mock_obj2, mock_obj3]
        MockTranslationModel.objects.filter.return_value = mock_queryset

        queryset = IndexQuerySet(MockTranslationModel)
        results = queryset.search("test")

        assert len(results["results"]) == 3

    @patch("meili.querysets.client")
    def test_combined_language_filter_and_locale(self, mock_client):
        """Test combined use of language filter and locale setting."""
        mock_client.get_index.return_value = self.mock_index

        mock_search_results = {
            "hits": [{"id": 1, "language_code": "el"}],
            "estimatedTotalHits": 1,
        }
        self.mock_index.search.return_value = mock_search_results

        mock_obj = MagicMock()
        mock_obj.pk = 1

        mock_queryset = MagicMock()
        mock_queryset.order_by.return_value = [mock_obj]
        MockTranslationModel.objects.filter.return_value = mock_queryset

        queryset = IndexQuerySet(MockTranslationModel)
        queryset.filter(language_code="el").locales("el")
        queryset.search("ελληνικά")

        call_args = self.mock_index.search.call_args[0][1]
        assert "language_code = 'el'" in call_args["filter"]
        assert call_args["locales"] == ["el"]

    @patch("meili.querysets.client")
    def test_language_filter_with_other_filters(self, mock_client):
        """Test language filter combined with other filters."""
        mock_client.get_index.return_value = self.mock_index

        mock_search_results = {
            "hits": [{"id": 1, "language_code": "en", "active": True}],
            "estimatedTotalHits": 1,
        }
        self.mock_index.search.return_value = mock_search_results

        mock_obj = MagicMock()
        mock_obj.pk = 1

        mock_queryset = MagicMock()
        mock_queryset.order_by.return_value = [mock_obj]
        MockTranslationModel.objects.filter.return_value = mock_queryset

        queryset = IndexQuerySet(MockTranslationModel)
        queryset.filter(language_code="en", active=True, likes_count__gte=10)
        queryset.search("test")

        call_args = self.mock_index.search.call_args[0][1]
        assert "language_code = 'en'" in call_args["filter"]
        assert "active = True" in call_args["filter"]
        assert "likes_count >= 10" in call_args["filter"]

    @patch("meili.querysets.client")
    def test_search_greek_characters(self, mock_client):
        """Test search with Greek characters."""
        mock_client.get_index.return_value = self.mock_index

        mock_search_results = {
            "hits": [
                {
                    "id": 1,
                    "title": "Δοκιμαστικό Κείμενο",
                    "language_code": "el",
                }
            ],
            "estimatedTotalHits": 1,
        }
        self.mock_index.search.return_value = mock_search_results

        mock_obj = MagicMock()
        mock_obj.pk = 1

        mock_queryset = MagicMock()
        mock_queryset.order_by.return_value = [mock_obj]
        MockTranslationModel.objects.filter.return_value = mock_queryset

        queryset = IndexQuerySet(MockTranslationModel)
        queryset.filter(language_code="el").locales("el")
        queryset.search("Δοκιμαστικό")

        self.mock_index.search.assert_called_once()
        call_args = self.mock_index.search.call_args[0]
        assert call_args[0] == "Δοκιμαστικό"

    @patch("meili.querysets.client")
    def test_search_german_characters(self, mock_client):
        """Test search with German characters (umlauts)."""
        mock_client.get_index.return_value = self.mock_index

        mock_search_results = {
            "hits": [
                {
                    "id": 1,
                    "title": "Über uns",
                    "language_code": "de",
                }
            ],
            "estimatedTotalHits": 1,
        }
        self.mock_index.search.return_value = mock_search_results

        mock_obj = MagicMock()
        mock_obj.pk = 1

        mock_queryset = MagicMock()
        mock_queryset.order_by.return_value = [mock_obj]
        MockTranslationModel.objects.filter.return_value = mock_queryset

        queryset = IndexQuerySet(MockTranslationModel)
        queryset.filter(language_code="de").locales("de")
        queryset.search("Über")

        self.mock_index.search.assert_called_once()
        call_args = self.mock_index.search.call_args[0]
        assert call_args[0] == "Über"

    @patch("meili.querysets.client")
    def test_empty_query_with_language_filter(self, mock_client):
        """Test empty query with language filter still filters by language."""
        mock_client.get_index.return_value = self.mock_index

        mock_search_results = {
            "hits": [{"id": 1, "language_code": "en"}],
            "estimatedTotalHits": 1,
        }
        self.mock_index.search.return_value = mock_search_results

        mock_obj = MagicMock()
        mock_obj.pk = 1

        mock_queryset = MagicMock()
        mock_queryset.order_by.return_value = [mock_obj]
        MockTranslationModel.objects.filter.return_value = mock_queryset

        queryset = IndexQuerySet(MockTranslationModel)
        queryset.filter(language_code="en")
        queryset.search("")

        call_args = self.mock_index.search.call_args[0][1]
        assert "language_code = 'en'" in call_args["filter"]
