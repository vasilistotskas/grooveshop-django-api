"""
Search analytics models for tracking search queries and user interactions.

This module provides models for:
- SearchQuery: Records every search query with metadata and results
- SearchClick: Records user clicks on search results for CTR analysis
"""

from django.db import models


class SearchQuery(models.Model):
    """
    Records every search query with metadata and results.

    This model tracks all search operations including product searches,
    blog post searches, and federated searches. It captures query details,
    results metadata, and optional user information for analytics.

    Indexes:
    - query, language_code (for query analysis)
    - timestamp, content_type (for time-based analysis)
    - results_count (for zero-result analysis)
    """

    CONTENT_TYPE_CHOICES = [
        ("product", "Product"),
        ("blog_post", "Blog Post"),
        ("federated", "Federated"),
    ]

    query = models.CharField(
        max_length=500,
        db_index=True,
        help_text="The search query text entered by the user",
    )
    language_code = models.CharField(
        max_length=10,
        db_index=True,
        null=True,
        blank=True,
        help_text="Language code for the search (e.g., 'en', 'el', 'de')",
    )
    content_type = models.CharField(
        max_length=20,
        choices=CONTENT_TYPE_CHOICES,
        db_index=True,
        help_text="Type of content being searched",
    )
    results_count = models.IntegerField(
        db_index=True, help_text="Number of results returned"
    )
    estimated_total_hits = models.IntegerField(
        help_text="Estimated total number of matching documents"
    )
    processing_time_ms = models.IntegerField(
        null=True,
        blank=True,
        help_text="Time taken to process the search in milliseconds",
    )

    # User tracking (optional)
    user = models.ForeignKey(
        "user.UserAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="search_queries",
        help_text="User who performed the search (if authenticated)",
    )
    session_key = models.CharField(
        max_length=40,
        null=True,
        blank=True,
        help_text="Session key for anonymous users",
    )
    ip_address = models.GenericIPAddressField(
        null=True, blank=True, help_text="IP address of the user"
    )
    user_agent = models.TextField(
        null=True, blank=True, help_text="User agent string from the request"
    )

    timestamp = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text="When the search was performed",
    )

    class Meta:
        db_table = "search_query"
        verbose_name = "Search Query"
        verbose_name_plural = "Search Queries"
        ordering = ["-timestamp"]
        indexes = [
            models.Index(
                fields=["query", "language_code"], name="search_query_lang_idx"
            ),
            models.Index(
                fields=["timestamp", "content_type"],
                name="search_time_type_idx",
            ),
        ]

    def __str__(self):
        return (
            f"{self.query} ({self.content_type}) - {self.results_count} results"
        )

    def __repr__(self):
        return (
            f"<SearchQuery(id={self.id}, query='{self.query}', "
            f"content_type='{self.content_type}', results={self.results_count})>"
        )


class SearchClick(models.Model):
    """
    Records user clicks on search results for CTR analysis.

    This model tracks when users click on search results, enabling
    click-through rate analysis and understanding which results are
    most relevant to users.

    Indexes:
    - search_query, timestamp (for CTR analysis)
    """

    RESULT_TYPE_CHOICES = [
        ("product", "Product"),
        ("blog_post", "Blog Post"),
    ]

    search_query = models.ForeignKey(
        SearchQuery,
        on_delete=models.CASCADE,
        related_name="clicks",
        help_text="The search query that led to this click",
    )
    result_id = models.CharField(
        max_length=100,
        help_text="ID of the clicked result (Product or BlogPost ID)",
    )
    result_type = models.CharField(
        max_length=20,
        choices=RESULT_TYPE_CHOICES,
        help_text="Type of result that was clicked",
    )
    position = models.IntegerField(
        help_text="Position of the result in search results (0-indexed)"
    )
    timestamp = models.DateTimeField(
        auto_now_add=True, db_index=True, help_text="When the click occurred"
    )

    class Meta:
        db_table = "search_click"
        verbose_name = "Search Click"
        verbose_name_plural = "Search Clicks"
        ordering = ["-timestamp"]
        indexes = [
            models.Index(
                fields=["search_query", "timestamp"],
                name="search_click_query_idx",
            ),
        ]

    def __str__(self):
        return (
            f"{self.result_type} {self.result_id} at position {self.position}"
        )

    def __repr__(self):
        return (
            f"<SearchClick(id={self.id}, result_type='{self.result_type}', "
            f"result_id='{self.result_id}', position={self.position})>"
        )
