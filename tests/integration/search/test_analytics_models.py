"""
Integration tests for search analytics models.

This module tests the SearchQuery and SearchClick models including:
- Model creation with all fields
- Foreign key relationships
- Database indexes
- Model methods and representations

Note: These tests require database access and are in tests/integration/
"""

import pytest
from django.db import connection

from search.models import SearchClick, SearchQuery
from user.factories.account import UserAccountFactory


@pytest.mark.django_db
class TestSearchQueryModel:
    """Test suite for SearchQuery model."""

    def test_search_query_creation_minimal(self):
        """
        Test creating SearchQuery with minimal required fields.

        Validates: Requirements 2.1, 2.2
        """
        search_query = SearchQuery.objects.create(
            query="laptop",
            content_type="product",
            results_count=10,
            estimated_total_hits=100,
        )

        assert search_query.id is not None
        assert search_query.query == "laptop"
        assert search_query.content_type == "product"
        assert search_query.results_count == 10
        assert search_query.estimated_total_hits == 100
        assert search_query.timestamp is not None

    def test_search_query_creation_all_fields(self):
        """
        Test creating SearchQuery with all fields populated.

        Validates: Requirements 2.1, 2.2
        """
        user = UserAccountFactory()

        search_query = SearchQuery.objects.create(
            query="gaming laptop",
            language_code="en",
            content_type="product",
            results_count=25,
            estimated_total_hits=250,
            processing_time_ms=150,
            user=user,
            session_key="abc123session",
            ip_address="192.168.1.1",
            user_agent="Mozilla/5.0",
        )

        assert search_query.id is not None
        assert search_query.query == "gaming laptop"
        assert search_query.language_code == "en"
        assert search_query.user == user

    @pytest.mark.parametrize(
        "content_type",
        ["product", "blog_post", "federated"],
    )
    def test_search_query_content_type_choices(self, content_type):
        """
        Test SearchQuery with different content_type choices.

        Validates: Requirements 2.1
        """
        search_query = SearchQuery.objects.create(
            query="test query",
            content_type=content_type,
            results_count=5,
            estimated_total_hits=50,
        )

        assert search_query.content_type == content_type

    def test_search_query_indexes_exist(self):
        """
        Test that database indexes are created for SearchQuery.

        Validates: Requirements 2.4
        """
        with connection.cursor() as cursor:
            table_name = SearchQuery._meta.db_table

            cursor.execute(
                """
                SELECT indexname 
                FROM pg_indexes 
                WHERE tablename = %s
                """,
                [table_name],
            )
            indexes = [row[0] for row in cursor.fetchall()]

        # Check for expected indexes
        assert any("query" in idx for idx in indexes), "query index not found"
        assert any("language_code" in idx for idx in indexes), (
            "language_code index not found"
        )
        assert any("content_type" in idx for idx in indexes), (
            "content_type index not found"
        )
        assert any("timestamp" in idx for idx in indexes), (
            "timestamp index not found"
        )


@pytest.mark.django_db
class TestSearchClickModel:
    """Test suite for SearchClick model."""

    def test_search_click_creation(self):
        """
        Test creating SearchClick with all required fields.

        Validates: Requirements 2.3
        """
        search_query = SearchQuery.objects.create(
            query="laptop",
            content_type="product",
            results_count=10,
            estimated_total_hits=100,
        )

        search_click = SearchClick.objects.create(
            search_query=search_query,
            result_id="12345",
            result_type="product",
            position=0,
        )

        assert search_click.id is not None
        assert search_click.search_query == search_query
        assert search_click.result_id == "12345"
        assert search_click.result_type == "product"
        assert search_click.position == 0

    @pytest.mark.parametrize(
        "result_type",
        ["product", "blog_post"],
    )
    def test_search_click_result_type_choices(self, result_type):
        """
        Test SearchClick with different result_type choices.

        Validates: Requirements 2.3
        """
        search_query = SearchQuery.objects.create(
            query="test",
            content_type="federated",
            results_count=10,
            estimated_total_hits=100,
        )

        search_click = SearchClick.objects.create(
            search_query=search_query,
            result_id="123",
            result_type=result_type,
            position=0,
        )

        assert search_click.result_type == result_type

    def test_search_click_cascade_delete(self):
        """
        Test that SearchClick is deleted when SearchQuery is deleted.

        Validates: Requirements 2.3
        """
        search_query = SearchQuery.objects.create(
            query="laptop",
            content_type="product",
            results_count=10,
            estimated_total_hits=100,
        )

        search_click = SearchClick.objects.create(
            search_query=search_query,
            result_id="123",
            result_type="product",
            position=0,
        )

        click_id = search_click.id
        search_query.delete()

        assert not SearchClick.objects.filter(id=click_id).exists()

    def test_search_click_indexes_exist(self):
        """
        Test that database indexes are created for SearchClick.

        Validates: Requirements 2.4
        """
        with connection.cursor() as cursor:
            table_name = SearchClick._meta.db_table

            cursor.execute(
                """
                SELECT indexname 
                FROM pg_indexes 
                WHERE tablename = %s
                """,
                [table_name],
            )
            indexes = [row[0] for row in cursor.fetchall()]

        # Check for expected indexes
        assert any("search_query" in idx for idx in indexes), (
            "search_query index not found"
        )
        assert any("timestamp" in idx for idx in indexes), (
            "timestamp index not found"
        )
