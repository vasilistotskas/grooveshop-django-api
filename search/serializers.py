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
