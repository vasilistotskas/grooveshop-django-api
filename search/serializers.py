from __future__ import annotations

from rest_framework import serializers

from blog.models.post import BlogPostTranslation
from product.models.product import ProductTranslation


class BlogPostTranslationSerializer(
    serializers.ModelSerializer[BlogPostTranslation]
):
    slug = serializers.SerializerMethodField()
    main_image_path = serializers.SerializerMethodField()
    matches_position = serializers.SerializerMethodField()
    ranking_score = serializers.SerializerMethodField()
    formatted = serializers.SerializerMethodField()
    content_type = serializers.SerializerMethodField()

    class Meta:
        model = BlogPostTranslation
        fields = (
            "id",
            "slug",
            "language_code",
            "title",
            "subtitle",
            "body",
            "master",
            "main_image_path",
            "matches_position",
            "ranking_score",
            "formatted",
            "content_type",
        )

    def get_slug(self, obj):
        return obj.master.slug if obj.master else ""

    def get_main_image_path(self, obj):
        return obj.master.main_image_path if obj.master else ""

    def get_matches_position(self, obj):
        return self.context.get("_matchesPosition", {})

    def get_ranking_score(self, obj):
        return self.context.get("_rankingScore", None)

    def get_formatted(self, obj):
        return self.context.get("_formatted", {})

    def get_content_type(self, obj):
        return "blog_post"


class ProductTranslationSerializer(
    serializers.ModelSerializer[ProductTranslation]
):
    slug = serializers.SerializerMethodField()
    main_image_path = serializers.SerializerMethodField()
    matches_position = serializers.SerializerMethodField()
    ranking_score = serializers.SerializerMethodField()
    formatted = serializers.SerializerMethodField()
    content_type = serializers.SerializerMethodField()
    # Add product fields from master
    final_price = serializers.SerializerMethodField()
    price = serializers.SerializerMethodField()
    discount_percent = serializers.SerializerMethodField()
    stock = serializers.SerializerMethodField()
    likes_count = serializers.SerializerMethodField()
    view_count = serializers.SerializerMethodField()
    review_average = serializers.SerializerMethodField()
    vat_percent = serializers.SerializerMethodField()

    class Meta:
        model = ProductTranslation
        fields = (
            "id",
            "slug",
            "language_code",
            "name",
            "description",
            "master",
            "main_image_path",
            "matches_position",
            "ranking_score",
            "formatted",
            "content_type",
            # Product fields
            "final_price",
            "price",
            "discount_percent",
            "stock",
            "likes_count",
            "view_count",
            "review_average",
            "vat_percent",
        )

    def get_slug(self, obj):
        return obj.master.slug if obj.master else ""

    def get_main_image_path(self, obj):
        return obj.master.main_image_path if obj.master else ""

    def get_matches_position(self, obj):
        return self.context.get("_matchesPosition", {})

    def get_ranking_score(self, obj):
        return self.context.get("_rankingScore", None)

    def get_formatted(self, obj):
        return self.context.get("_formatted", {})

    def get_content_type(self, obj):
        return "product"

    def get_final_price(self, obj):
        if obj.master and obj.master.final_price:
            return float(obj.master.final_price.amount)
        return None

    def get_price(self, obj):
        if obj.master and obj.master.price:
            return float(obj.master.price.amount)
        return None

    def get_discount_percent(self, obj):
        return obj.master.discount_percent if obj.master else None

    def get_stock(self, obj):
        return obj.master.stock if obj.master else 0

    def get_likes_count(self, obj):
        return obj.master.likes_count if obj.master else 0

    def get_view_count(self, obj):
        return obj.master.view_count if obj.master else 0

    def get_review_average(self, obj):
        return obj.master.review_average if obj.master else None

    def get_vat_percent(self, obj):
        if obj.master and obj.master.vat:
            return float(obj.master.vat.value)
        return None


class BlogPostMeiliSearchResultSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    language_code = serializers.CharField()
    title = serializers.CharField()
    subtitle = serializers.CharField()
    body = serializers.CharField()
    master = serializers.IntegerField()
    slug = serializers.CharField()
    main_image_path = serializers.CharField()
    matches_position = serializers.JSONField()
    ranking_score = serializers.FloatField(allow_null=True)
    formatted = serializers.JSONField()
    content_type = serializers.CharField()


class ProductMeiliSearchResultSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    language_code = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()
    master = serializers.IntegerField()
    slug = serializers.CharField()
    main_image_path = serializers.CharField()
    matches_position = serializers.JSONField()
    ranking_score = serializers.FloatField(allow_null=True)
    formatted = serializers.JSONField()
    content_type = serializers.CharField()
    # Product fields
    final_price = serializers.FloatField(allow_null=True)
    price = serializers.FloatField(allow_null=True)
    discount_percent = serializers.IntegerField(allow_null=True)
    stock = serializers.IntegerField()
    likes_count = serializers.IntegerField()
    view_count = serializers.IntegerField()
    review_average = serializers.FloatField(allow_null=True)
    vat_percent = serializers.FloatField(allow_null=True)


class BlogPostMeiliSearchResponseSerializer(serializers.Serializer):
    limit = serializers.IntegerField()
    offset = serializers.IntegerField()
    estimated_total_hits = serializers.IntegerField()
    results = BlogPostMeiliSearchResultSerializer(many=True)


class FacetStatsItemSerializer(serializers.Serializer):
    """Serializer for individual facet stat (min/max values)."""

    min = serializers.FloatField()
    max = serializers.FloatField()


class FacetStatsSerializer(serializers.Serializer):
    """Serializer for facet statistics (numeric facets)."""

    final_price = FacetStatsItemSerializer(required=False)
    likes_count = FacetStatsItemSerializer(required=False)
    view_count = FacetStatsItemSerializer(required=False)


class ProductMeiliSearchResponseSerializer(serializers.Serializer):
    limit = serializers.IntegerField()
    offset = serializers.IntegerField()
    estimated_total_hits = serializers.IntegerField()
    results = ProductMeiliSearchResultSerializer(many=True)
    facet_distribution = serializers.DictField(
        child=serializers.DictField(child=serializers.IntegerField()),
        required=False,
        help_text="Facet distribution with counts per category/value",
    )
    facet_stats = FacetStatsSerializer(
        required=False,
        help_text="Facet statistics with min/max values for numeric fields",
    )


class FederationMetadataSerializer(serializers.Serializer):
    """Serializer for federation metadata from Meilisearch."""

    index_uid = serializers.CharField(
        source="indexUid", help_text="Index UID where the result originated"
    )
    queries_position = serializers.IntegerField(
        source="queriesPosition",
        help_text="Position of the query in the multi_search request",
    )
    weighted_ranking_score = serializers.FloatField(
        source="weightedRankingScore",
        help_text="Ranking score after applying federation weight",
    )


class FederatedSearchResultSerializer(serializers.Serializer):
    """
    Serializer for individual federated search result.

    This combines fields from both ProductTranslation and BlogPostTranslation
    with federation metadata.
    """

    id = serializers.IntegerField()
    language_code = serializers.CharField()
    content_type = serializers.CharField(
        help_text="Type of content: 'product' or 'blog_post'"
    )

    # Common fields
    slug = serializers.CharField(required=False)
    main_image_path = serializers.CharField(required=False)
    matches_position = serializers.JSONField()
    ranking_score = serializers.FloatField(allow_null=True)
    formatted = serializers.JSONField()

    # Product-specific fields (optional)
    name = serializers.CharField(required=False)
    description = serializers.CharField(required=False)
    final_price = serializers.FloatField(allow_null=True, required=False)
    price = serializers.FloatField(allow_null=True, required=False)
    discount_percent = serializers.IntegerField(allow_null=True, required=False)
    stock = serializers.IntegerField(required=False)
    likes_count = serializers.IntegerField(required=False)
    view_count = serializers.IntegerField(required=False)
    review_average = serializers.FloatField(allow_null=True, required=False)
    vat_percent = serializers.FloatField(allow_null=True, required=False)

    # Blog post-specific fields (optional)
    title = serializers.CharField(required=False)
    subtitle = serializers.CharField(required=False)
    body = serializers.CharField(required=False)

    # Master reference
    master = serializers.IntegerField(required=False)

    # Federation metadata
    _federation = FederationMetadataSerializer(
        help_text="Federation metadata from Meilisearch"
    )


class FederatedSearchResponseSerializer(serializers.Serializer):
    """Serializer for federated search response."""

    limit = serializers.IntegerField(
        help_text="Maximum number of results requested"
    )
    offset = serializers.IntegerField(help_text="Number of results skipped")
    estimated_total_hits = serializers.IntegerField(
        help_text="Estimated total number of matching documents across all indexes"
    )
    results = FederatedSearchResultSerializer(
        many=True,
        help_text="Unified search results from products and blog posts",
    )


class TopQuerySerializer(serializers.Serializer):
    """Serializer for top query analytics."""

    query = serializers.CharField(help_text="The search query text")
    count = serializers.IntegerField(
        help_text="Number of times this query was searched"
    )
    avg_results = serializers.FloatField(
        help_text="Average number of results returned for this query"
    )
    click_through_rate = serializers.FloatField(
        help_text="Click-through rate (clicks / searches) for this query"
    )


class ZeroResultQuerySerializer(serializers.Serializer):
    """Serializer for zero-result query analytics."""

    query = serializers.CharField(
        help_text="The search query text that returned no results"
    )
    count = serializers.IntegerField(
        help_text="Number of times this query returned zero results"
    )
    language_code = serializers.CharField(
        help_text="Language code for the query"
    )


class DateRangeSerializer(serializers.Serializer):
    """Serializer for date range in analytics."""

    start = serializers.CharField(
        help_text="Start date of the analytics range (ISO format or 'all')"
    )
    end = serializers.CharField(
        help_text="End date of the analytics range (ISO format or 'now')"
    )


class SearchVolumeSerializer(serializers.Serializer):
    """Serializer for search volume metrics."""

    total = serializers.IntegerField(
        help_text="Total number of searches in the date range"
    )
    by_content_type = serializers.DictField(
        child=serializers.IntegerField(),
        help_text="Search volume breakdown by content type (product, blog_post, federated)",
    )
    by_language = serializers.DictField(
        child=serializers.IntegerField(),
        help_text="Search volume breakdown by language code",
    )


class PerformanceMetricsSerializer(serializers.Serializer):
    """Serializer for search performance metrics."""

    avg_processing_time_ms = serializers.FloatField(
        help_text="Average search processing time in milliseconds"
    )
    avg_results_count = serializers.FloatField(
        help_text="Average number of results returned per search"
    )


class SearchAnalyticsResponseSerializer(serializers.Serializer):
    """Serializer for search analytics response."""

    date_range = DateRangeSerializer(
        help_text="Date range for the analytics data"
    )
    top_queries = TopQuerySerializer(
        many=True, help_text="Top 20 queries by frequency with CTR metrics"
    )
    zero_result_queries = ZeroResultQuerySerializer(
        many=True, help_text="Queries that returned zero results"
    )
    search_volume = SearchVolumeSerializer(
        help_text="Search volume metrics by content type and language"
    )
    performance = PerformanceMetricsSerializer(
        help_text="Average performance metrics"
    )
    click_through_rate = serializers.FloatField(
        help_text="Overall click-through rate (total clicks / total searches)"
    )
