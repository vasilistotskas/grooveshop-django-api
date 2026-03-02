from __future__ import annotations

from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response

from core.api.views import BaseModelViewSet
from core.utils.serializers import ActionConfig
from page_config.models import PageLayout, PageSection
from page_config.serializers import (
    PageLayoutAdminSerializer,
    PageLayoutSerializer,
)


@extend_schema(
    responses=PageLayoutSerializer,
    tags=["Page Config"],
)
@api_view(["GET"])
@permission_classes([AllowAny])
def public_page_config(request, page_type):
    layout = get_object_or_404(
        PageLayout.objects.published().prefetch_related(
            Prefetch(
                "sections",
                queryset=PageSection.objects.filter(is_visible=True).order_by(
                    "sort_order"
                ),
            ),
        ),
        page_type=page_type,
    )
    serializer = PageLayoutSerializer(layout)
    return Response(serializer.data)


class PageLayoutAdminViewSet(BaseModelViewSet):
    queryset = PageLayout.objects.prefetch_related("sections")
    permission_classes = [IsAdminUser]
    serializers_config = {
        "list": ActionConfig(response=PageLayoutSerializer),
        "retrieve": ActionConfig(response=PageLayoutSerializer),
        "create": ActionConfig(
            request=PageLayoutAdminSerializer,
            response=PageLayoutSerializer,
        ),
        "update": ActionConfig(
            request=PageLayoutAdminSerializer,
            response=PageLayoutSerializer,
        ),
        "partial_update": ActionConfig(
            request=PageLayoutAdminSerializer,
            response=PageLayoutSerializer,
        ),
        "destroy": ActionConfig(response=PageLayoutSerializer),
    }
