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


class BlogPostMeiliSearchResponseSerializer(serializers.Serializer):
    limit = serializers.IntegerField()
    offset = serializers.IntegerField()
    estimated_total_hits = serializers.IntegerField()
    results = BlogPostMeiliSearchResultSerializer(many=True)


class ProductMeiliSearchResponseSerializer(serializers.Serializer):
    limit = serializers.IntegerField()
    offset = serializers.IntegerField()
    estimated_total_hits = serializers.IntegerField()
    results = ProductMeiliSearchResultSerializer(many=True)
