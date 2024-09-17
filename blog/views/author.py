from __future__ import annotations

from django.conf import settings
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter

from blog.models.author import BlogAuthor
from blog.serializers.author import BlogAuthorSerializer
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.views import cache_methods


@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class BlogAuthorViewSet(BaseModelViewSet):
    queryset = BlogAuthor.objects.all()
    serializer_class = BlogAuthorSerializer
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_fields = ["id", "user"]
    ordering_fields = ["id", "user", "created_at"]
    ordering = ["-created_at"]
    search_fields = ["id", "user"]
