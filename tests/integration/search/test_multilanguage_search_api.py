from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from blog.models.post import BlogPostTranslation
from product.models.product import ProductTranslation


@pytest.mark.django_db
class TestBlogPostMultilanguageSearch:
    """Test blog post search with multilanguage support."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = APIClient()
        self.url = reverse("search-blog-post")

    @patch.object(BlogPostTranslation.meilisearch, "search")
    def test_search_with_language_filter_english(self, mock_search):
        """Test blog post search filtered by English language."""
        mock_search.return_value = {
            "results": [],
            "estimated_total_hits": 0,
            "offset": 0,
            "limit": 10,
        }

        response = self.client.get(
            self.url, {"query": "test", "language_code": "en"}
        )

        assert response.status_code == status.HTTP_200_OK
        assert "results" in response.data
        assert response.data["limit"] == 10
        assert response.data["offset"] == 0

    @patch.object(BlogPostTranslation.meilisearch, "search")
    def test_search_with_language_filter_greek(self, mock_search):
        """Test blog post search filtered by Greek language."""
        mock_search.return_value = {
            "results": [],
            "estimated_total_hits": 0,
            "offset": 0,
            "limit": 10,
        }

        response = self.client.get(
            self.url, {"query": "δοκιμή", "language_code": "el"}
        )

        assert response.status_code == status.HTTP_200_OK

    @patch.object(BlogPostTranslation.meilisearch, "search")
    def test_search_with_language_filter_german(self, mock_search):
        """Test blog post search filtered by German language."""
        mock_search.return_value = {
            "results": [],
            "estimated_total_hits": 0,
            "offset": 0,
            "limit": 10,
        }

        response = self.client.get(
            self.url, {"query": "über", "language_code": "de"}
        )

        assert response.status_code == status.HTTP_200_OK

    @patch.object(BlogPostTranslation.meilisearch, "search")
    def test_search_without_language_filter(self, mock_search):
        """Test blog post search without language filter."""
        mock_search.return_value = {
            "results": [],
            "estimated_total_hits": 0,
            "offset": 0,
            "limit": 10,
        }

        response = self.client.get(self.url, {"query": "test"})

        assert response.status_code == status.HTTP_200_OK

    def test_search_without_query_parameter(self):
        """Test search without query parameter returns error."""
        response = self.client.get(self.url)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch.object(BlogPostTranslation.meilisearch, "search")
    def test_search_with_pagination_and_language(self, mock_search):
        """Test search with pagination and language filter."""
        mock_search.return_value = {
            "results": [],
            "estimated_total_hits": 50,
            "offset": 10,
            "limit": 20,
        }

        response = self.client.get(
            self.url,
            {"query": "test", "language_code": "en", "limit": 20, "offset": 10},
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["limit"] == 20
        assert response.data["offset"] == 10
        assert response.data["estimated_total_hits"] == 50


@pytest.mark.django_db
class TestProductMultilanguageSearch:
    """Test product search with multilanguage support."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = APIClient()
        self.url = reverse("search-product")

    @patch.object(ProductTranslation.meilisearch, "search")
    def test_search_with_language_filter_english(self, mock_search):
        """Test product search filtered by English language."""
        mock_search.return_value = {
            "results": [],
            "estimated_total_hits": 0,
            "offset": 0,
            "limit": 10,
        }

        response = self.client.get(
            self.url, {"query": "laptop", "language_code": "en"}
        )

        assert response.status_code == status.HTTP_200_OK
        assert "results" in response.data

    @patch.object(ProductTranslation.meilisearch, "search")
    def test_search_with_language_filter_greek(self, mock_search):
        """Test product search filtered by Greek language."""
        mock_search.return_value = {
            "results": [],
            "estimated_total_hits": 0,
            "offset": 0,
            "limit": 10,
        }

        response = self.client.get(
            self.url, {"query": "υπολογιστής", "language_code": "el"}
        )

        assert response.status_code == status.HTTP_200_OK

    @patch.object(ProductTranslation.meilisearch, "search")
    def test_search_with_language_filter_german(self, mock_search):
        """Test product search filtered by German language."""
        mock_search.return_value = {
            "results": [],
            "estimated_total_hits": 0,
            "offset": 0,
            "limit": 10,
        }

        response = self.client.get(
            self.url, {"query": "computer", "language_code": "de"}
        )

        assert response.status_code == status.HTTP_200_OK

    @patch.object(ProductTranslation.meilisearch, "search")
    def test_search_without_language_filter(self, mock_search):
        """Test product search without language filter."""
        mock_search.return_value = {
            "results": [],
            "estimated_total_hits": 0,
            "offset": 0,
            "limit": 10,
        }

        response = self.client.get(self.url, {"query": "product"})

        assert response.status_code == status.HTTP_200_OK

    def test_search_without_query_parameter(self):
        """Test search without query parameter returns error."""
        response = self.client.get(self.url)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch.object(ProductTranslation.meilisearch, "search")
    def test_search_with_pagination_and_language(self, mock_search):
        """Test search with pagination and language filter."""
        mock_search.return_value = {
            "results": [],
            "estimated_total_hits": 100,
            "offset": 20,
            "limit": 25,
        }

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
        assert response.data["estimated_total_hits"] == 100

    @patch.object(ProductTranslation.meilisearch, "search")
    def test_search_url_encoded_query(self, mock_search):
        mock_search.return_value = {
            "results": [],
            "estimated_total_hits": 0,
            "offset": 0,
            "limit": 10,
        }

        response = self.client.get(
            self.url, {"query": "laptop%20computer", "language_code": "en"}
        )

        assert response.status_code == status.HTTP_200_OK
