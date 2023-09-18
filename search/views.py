from django.conf import settings
from django.contrib.postgres.search import SearchHeadline
from django.contrib.postgres.search import SearchQuery
from django.contrib.postgres.search import SearchRank
from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import F
from django.db.models import Q
from rest_framework import status
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from product.models.product import Product
from search.serializers import SearchProductSerializer

default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class SearchProduct(ModelViewSet):
    serializer_class = SearchProductSerializer
    limit = 20

    def get_queryset(self):
        query = self.request.query_params.get("query", None)
        language = self.request.query_params.get("language", default_language)

        if query:
            search_query = SearchQuery(query, search_type="websearch", config="simple")

            lookup = (
                Q(search_vector=search_query)
                | Q(translations__name__search=search_query)
                | Q(translations__description__search=search_query)
                | Q(slug__search=search_query)
            ) & Q(translations__language_code=language)

            queryset = (
                Product.objects.only("id", "slug")
                .prefetch_related("translations")
                .filter(lookup)
                .annotate(
                    search_rank=SearchRank(F("search_vector"), search_query),
                    headline=SearchHeadline(
                        "translations__name",
                        search_query,
                        start_sel="<span>",
                        stop_sel="</span>",
                    ),
                    similarity=TrigramSimilarity("translations__name", query),
                )
                .filter(similarity__gt=0.3)
                .order_by("-similarity")
            )
            return queryset

        return Product.objects.none()

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())

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
            result_count_data = queryset.count()

            serializer = self.get_serializer(
                {
                    "results": results_data,
                    "headlines": headlines_data,
                    "search_ranks": search_ranks_data,
                    "result_count": result_count_data,
                    "similarities": similarities_data,
                }
            )
            return Response(serializer.data, status=status.HTTP_200_OK)

        return Response(status=status.HTTP_204_NO_CONTENT)
