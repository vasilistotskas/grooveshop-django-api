import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from tests.conftest import requires_meilisearch


@requires_meilisearch
@pytest.mark.django_db
class TestBlogPostMultilanguageSearch:
    """Test blog post search with multilanguage support.

    NOTE: These tests require a running Meilisearch instance.
    They are skipped in CI environments where Meilisearch is not available.
    """

    def setup_method(self):
        """Set up test fixtures."""
        self.client = APIClient()
        self.url = reverse("search-blog-post")

    def test_search_with_language_filter_english(self):
        """Test blog post search filtered by English language."""
        response = self.client.get(
            self.url, {"query": "test", "language_code": "en"}
        )

        assert response.status_code == status.HTTP_200_OK
        assert "results" in response.data
        assert "limit" in response.data
        assert "offset" in response.data

    def test_search_with_language_filter_greek(self):
        """Test blog post search filtered by Greek language."""
        response = self.client.get(
            self.url, {"query": "δοκιμή", "language_code": "el"}
        )

        assert response.status_code == status.HTTP_200_OK

    def test_search_with_language_filter_german(self):
        """Test blog post search filtered by German language."""
        response = self.client.get(
            self.url, {"query": "über", "language_code": "de"}
        )

        assert response.status_code == status.HTTP_200_OK

    def test_search_without_language_filter(self):
        """Test blog post search without language filter."""
        response = self.client.get(self.url, {"query": "test"})

        assert response.status_code == status.HTTP_200_OK

    def test_search_without_query_parameter(self):
        """Test search without query parameter returns error."""
        response = self.client.get(self.url)

        # Blog post search requires query parameter
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_search_with_pagination_and_language(self):
        """Test search with pagination and language filter."""
        response = self.client.get(
            self.url,
            {"query": "test", "language_code": "en", "limit": 20, "offset": 10},
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["limit"] == 20
        assert response.data["offset"] == 10
        # Don't assert exact count - it depends on real data
        assert "estimated_total_hits" in response.data


@requires_meilisearch
@pytest.mark.django_db
class TestProductMultilanguageSearch:
    """Test product search with multilanguage support.

    NOTE: These tests require a running Meilisearch instance.
    They are skipped in CI environments where Meilisearch is not available.
    """

    def setup_method(self):
        """Set up test fixtures."""
        self.client = APIClient()
        self.url = reverse("search-product")

    def test_search_with_language_filter_english(self):
        """Test product search filtered by English language."""
        response = self.client.get(
            self.url, {"query": "laptop", "language_code": "en"}
        )

        assert response.status_code == status.HTTP_200_OK
        assert "results" in response.data

    def test_search_with_language_filter_greek(self):
        """Test product search filtered by Greek language."""
        response = self.client.get(
            self.url, {"query": "υπολογιστής", "language_code": "el"}
        )

        assert response.status_code == status.HTTP_200_OK

    def test_search_with_language_filter_german(self):
        """Test product search filtered by German language."""
        response = self.client.get(
            self.url, {"query": "computer", "language_code": "de"}
        )

        assert response.status_code == status.HTTP_200_OK

    def test_search_without_language_filter(self):
        """Test product search without language filter."""
        response = self.client.get(self.url, {"query": "product"})

        assert response.status_code == status.HTTP_200_OK

    def test_search_without_query_parameter(self):
        """Test product search without query parameter returns all products (query is optional)."""
        response = self.client.get(self.url)

        # Product search allows empty query (returns all products with filters)
        assert response.status_code == status.HTTP_200_OK
        assert "results" in response.data
        assert "estimated_total_hits" in response.data

    def test_search_with_pagination_and_language(self):
        """Test search with pagination and language filter."""
        response = self.client.get(
            self.url,
            {
                "query": "product",
                "language_code": "en",
                "limit": 25,
                "offset": 20,
            },
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["limit"] == 25
        assert response.data["offset"] == 20
        # Don't assert exact count - use dynamic assertion based on real data
        assert response.data["estimated_total_hits"] >= 0

    def test_search_url_encoded_query(self):
        """Test search with URL encoded query."""
        response = self.client.get(
            self.url, {"query": "laptop%20computer", "language_code": "en"}
        )

        assert response.status_code == status.HTTP_200_OK
