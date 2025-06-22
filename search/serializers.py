from __future__ import annotations

from rest_framework import serializers

from blog.models.post import BlogPostTranslation
from product.models.product import ProductTranslation


class BlogPostTranslationSerializer(
    serializers.ModelSerializer[BlogPostTranslation]
):
    main_image_path = serializers.SerializerMethodField()
    matches_position = serializers.SerializerMethodField()
    ranking_score = serializers.SerializerMethodField()
    formatted = serializers.SerializerMethodField()

    class Meta:
        model = BlogPostTranslation
        fields = (
            "id",
            "language_code",
            "title",
            "subtitle",
            "body",
            "master",
            "main_image_path",
            "matches_position",
            "ranking_score",
            "formatted",
        )

    def get_main_image_path(self, obj):
        return obj.master.main_image_path if obj.master else ""

    def get_matches_position(self, obj):
        return self.context.get("_matchesPosition", {})

    def get_ranking_score(self, obj):
        return self.context.get("_rankingScore", None)

    def get_formatted(self, obj):
        return self.context.get("_formatted", {})


class ProductTranslationSerializer(
    serializers.ModelSerializer[ProductTranslation]
):
    main_image_path = serializers.SerializerMethodField()
    matches_position = serializers.SerializerMethodField()
    ranking_score = serializers.SerializerMethodField()
    formatted = serializers.SerializerMethodField()

    class Meta:
        model = ProductTranslation
        fields = (
            "id",
            "language_code",
            "name",
            "description",
            "master",
            "main_image_path",
            "matches_position",
            "ranking_score",
            "formatted",
        )

    def get_main_image_path(self, obj):
        return obj.master.main_image_path if obj.master else ""

    def get_matches_position(self, obj):
        return self.context.get("_matchesPosition", {})

    def get_ranking_score(self, obj):
        return self.context.get("_rankingScore", None)

    def get_formatted(self, obj):
        return self.context.get("_formatted", {})


class BlogPostMeiliSearchResultSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    language_code = serializers.CharField()
    title = serializers.CharField()
    subtitle = serializers.CharField()
    body = serializers.CharField()
    master = serializers.IntegerField()
    main_image_path = serializers.CharField()
    matches_position = serializers.JSONField()
    ranking_score = serializers.FloatField(allow_null=True)
    formatted = serializers.JSONField()


class ProductMeiliSearchResultSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    language_code = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()
    master = serializers.IntegerField()
    main_image_path = serializers.CharField()
    matches_position = serializers.JSONField()
    ranking_score = serializers.FloatField(allow_null=True)
    formatted = serializers.JSONField()


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
