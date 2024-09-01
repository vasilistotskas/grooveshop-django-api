from __future__ import annotations

from rest_framework import serializers

from blog.models.post import BlogPostTranslation
from product.models.product import ProductTranslation


class BlogPostTranslationSerializer(serializers.ModelSerializer):
    absolute_url = serializers.SerializerMethodField()
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
            "absolute_url",
            "main_image_path",
            "matches_position",
            "ranking_score",
            "formatted",
        )

    def get_absolute_url(self, obj):
        return obj.master.absolute_url if obj.master else ""

    def get_main_image_path(self, obj):
        return obj.master.main_image_path if obj.master else ""

    def get_matches_position(self, obj):
        return self.context.get("_matchesPosition", {})

    def get_ranking_score(self, obj):
        return self.context.get("_rankingScore", None)

    def get_formatted(self, obj):
        return self.context.get("_formatted", {})


class ProductTranslationSerializer(serializers.ModelSerializer):
    absolute_url = serializers.SerializerMethodField()
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
            "absolute_url",
            "main_image_path",
            "matches_position",
            "ranking_score",
            "formatted",
        )

    def get_absolute_url(self, obj):
        return obj.master.absolute_url if obj.master else ""

    def get_main_image_path(self, obj):
        return obj.master.main_image_path if obj.master else ""

    def get_matches_position(self, obj):
        return self.context.get("_matchesPosition", {})

    def get_ranking_score(self, obj):
        return self.context.get("_rankingScore", None)

    def get_formatted(self, obj):
        return self.context.get("_formatted", {})
