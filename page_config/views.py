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
from tenant.membership import HasTenantAccess


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
    # ``IsAdminUser`` alone lets any platform-staff user mutate any
    # tenant's page layout (H22 in MULTI_TENANT_AUDIT.md). Pair it
    # with ``HasTenantAccess`` so the requester must also be a member
    # of the current tenant — platform owners onboarding a new tenant
    # get a membership provisioned through the standard flow.
    permission_classes = [IsAdminUser, HasTenantAccess]
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
