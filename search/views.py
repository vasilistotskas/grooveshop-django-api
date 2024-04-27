from __future__ import annotations

from urllib.parse import unquote

from django.conf import settings
from django.contrib.postgres.search import SearchHeadline
from django.contrib.postgres.search import SearchQuery
from django.contrib.postgres.search import SearchRank
from django.contrib.postgres.search import TrigramWordDistance
from django.contrib.postgres.search import TrigramWordSimilarity
from django.db.models import F
from django.db.models import Q
from django.utils.decorators import method_decorator
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.viewsets import ReadOnlyModelViewSet

from blog.models.post import BlogPost
from core.utils.views import conditional_cache_page
from product.models.product import Product
from search.serializers import SearchBlogPostSerializer
from search.serializers import SearchProductSerializer

default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE

DEFAULT_SEARCH_CACHE_TTL = 60 * 5


class SearchProduct(ReadOnlyModelViewSet):
    serializer_class = SearchProductSerializer
    limit = 20
    http_method_names = ["get"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Product.objects.none()

        query = self.request.query_params.get("query")

        language = self.request.query_params.get(
            "language", settings.PARLER_DEFAULT_LANGUAGE_CODE
        )

        self.validate_language(language)
        if not query:
            raise ValidationError({"error": "A search query is required."})

        decoded_query = unquote(query)

        config = self.get_postgres_search_config(language)
        return self.get_filtered_queryset(decoded_query, language, config)

    def validate_language(self, language: str):
        supported_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]
        if language not in supported_languages:
            raise ValidationError({"error": "Unsupported language."})

    def get_postgres_search_config(self, language_code: str) -> str:
        language_configs = settings.PARLER_LANGUAGES.get(settings.SITE_ID, ())
        for lang_config in language_configs:
            if lang_config.get("code") == language_code:
                return lang_config.get("name", "").lower()
        return "simple"

    def get_filtered_queryset(self, query: str, language: str, config: str):
        search_query = SearchQuery(query, search_type="websearch", config=config)

        lookup = (
            Q(translations__search_vector=search_query)
            | Q(translations__search_document__icontains=query)
        ) & Q(translations__language_code=language)

        queryset = (
            Product.objects.filter(lookup)
            .prefetch_related("translations")
            .annotate(
                search_rank=SearchRank(F("translations__search_vector"), search_query),
                similarity=TrigramWordSimilarity(
                    query, F("translations__search_document")
                ),
                headline=SearchHeadline(
                    "translations__name",
                    search_query,
                    start_sel="<mark>",
                    stop_sel="</mark>",
                    max_words=30,
                    config=config,
                ),
                distance=TrigramWordDistance(query, F("translations__search_document")),
            )
            .filter(
                Q(search_rank__gte=0.1) | Q(similarity__gte=0.1),
                distance__lte=0.5,
                translations__language_code=language,
            )
            .order_by("-search_rank", "-similarity")
        )
        return queryset

    @method_decorator(conditional_cache_page(DEFAULT_SEARCH_CACHE_TTL))
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())

        if isinstance(queryset, Response):
            return queryset

        if queryset.exists():
            limited_queryset = queryset[: self.limit]

            results_data = limited_queryset
            headlines_data = {result.id: result.headline for result in limited_queryset}
            search_ranks_data = {
                result.id: result.search_rank for result in limited_queryset
            }
            similarities_data = {
                result.id: result.similarity for result in limited_queryset
            }
            distances_data = {result.id: result.distance for result in limited_queryset}
            result_count_data = min(queryset.count(), self.limit)

            serializer = self.get_serializer(
                {
                    "results": results_data,
                    "headlines": headlines_data,
                    "search_ranks": search_ranks_data,
                    "result_count": result_count_data,
                    "similarities": similarities_data,
                    "distances": distances_data,
                }
            )
            return Response(serializer.data, status=status.HTTP_200_OK)

        return Response(status=status.HTTP_204_NO_CONTENT)


class SearchBlogPost(ReadOnlyModelViewSet):
    serializer_class = SearchBlogPostSerializer
    limit = 20
    http_method_names = ["get"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return BlogPost.objects.none()

        query = self.request.query_params.get("query")

        language = self.request.query_params.get(
            "language", settings.PARLER_DEFAULT_LANGUAGE_CODE
        )

        self.validate_language(language)
        if not query:
            raise ValidationError({"error": "A search query is required."})

        decoded_query = unquote(query)

        config = self.get_postgres_search_config(language)
        return self.get_filtered_queryset(decoded_query, language, config)

    def validate_language(self, language: str):
        supported_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]
        if language not in supported_languages:
            raise ValidationError({"error": "Unsupported language."})

    def get_postgres_search_config(self, language_code: str) -> str:
        language_configs = settings.PARLER_LANGUAGES.get(settings.SITE_ID, ())
        for lang_config in language_configs:
            if lang_config.get("code") == language_code:
                return lang_config.get("name", "").lower()
        return "simple"

    def get_filtered_queryset(self, query: str, language: str, config: str):
        search_query = SearchQuery(query, search_type="websearch", config=config)

        lookup = (
            Q(translations__search_vector=search_query)
            | Q(translations__search_document__icontains=query)
        ) & Q(translations__language_code=language)

        queryset = (
            BlogPost.objects.filter(lookup)
            .prefetch_related("translations")
            .annotate(
                search_rank=SearchRank(F("translations__search_vector"), search_query),
                similarity=TrigramWordSimilarity(
                    query, F("translations__search_document")
                ),
                headline=SearchHeadline(
                    "translations__title",
                    search_query,
                    start_sel="<mark>",
                    stop_sel="</mark>",
                    max_words=30,
                    config=config,
                ),
                distance=TrigramWordDistance(query, F("translations__search_document")),
            )
            .filter(
                Q(search_rank__gte=0.1) | Q(similarity__gte=0.1),
                distance__lte=0.5,
                translations__language_code=language,
            )
            .order_by("-search_rank", "-similarity")
        )

        return queryset

    @method_decorator(conditional_cache_page(DEFAULT_SEARCH_CACHE_TTL))
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())

        if isinstance(queryset, Response):
            return queryset

        if queryset.exists():
            limited_queryset = queryset[: self.limit]

            results_data = limited_queryset
            headlines_data = {result.id: result.headline for result in limited_queryset}
            search_ranks_data = {
                result.id: result.search_rank for result in limited_queryset
            }
            similarities_data = {
                result.id: result.similarity for result in limited_queryset
            }
            distances_data = {result.id: result.distance for result in limited_queryset}
            result_count_data = min(queryset.count(), self.limit)

            serializer = self.get_serializer(
                {
                    "results": results_data,
                    "headlines": headlines_data,
                    "search_ranks": search_ranks_data,
                    "result_count": result_count_data,
                    "similarities": similarities_data,
                    "distances": distances_data,
                }
            )
            return Response(serializer.data, status=status.HTTP_200_OK)

        return Response(status=status.HTTP_204_NO_CONTENT)
