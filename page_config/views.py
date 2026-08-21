from __future__ import annotations

from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from core.api.permissions import IsPlatformSuperuser
from core.api.views import BaseModelViewSet
from core.utils.serializers import ActionConfig
from page_config.models import NavigationMenu, PageLayout, PageSection
from page_config.serializers import (
    NavigationMenuSerializer,
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


@extend_schema(
    responses={
        200: {
            "type": "object",
            "additionalProperties": {"type": "array", "items": {}},
            "description": "Navigation items keyed by slot "
            "(header/footer/mobile). Missing slots mean 'use the "
            "storefront's built-in menu'.",
        }
    },
    tags=["Page Config"],
)
@api_view(["GET"])
@permission_classes([AllowAny])
def public_navigation(request):
    """All configured navigation menus for the current tenant, keyed by
    slot. Slots without a row are OMITTED — the storefront renders its
    code-level menus for those, so an unconfigured tenant keeps the
    platform chrome untouched."""
    return Response(
        {
            menu.slot: menu.items
            for menu in NavigationMenu.objects.all()
            if menu.items
        }
    )


class NavigationMenuAdminViewSet(BaseModelViewSet):
    queryset = NavigationMenu.objects.all()
    # Platform-only, same rationale as PageLayoutAdminViewSet below.
    permission_classes = [IsPlatformSuperuser]
    # One entry per ACTION. ``BaseModelViewSet.get_serializer_class``
    # looks the current action up by name (core/api/views.py) — there is
    # no "default" key anywhere in the codebase, so every one of these
    # six routes raised ImproperlyConfigured and 500'd, and schema
    # generation emitted an error per action instead of a request body.
    # Nothing consumes them yet, which is why it went unnoticed.
    serializers_config = {
        "list": ActionConfig(response=NavigationMenuSerializer),
        "retrieve": ActionConfig(response=NavigationMenuSerializer),
        "create": ActionConfig(
            request=NavigationMenuSerializer,
            response=NavigationMenuSerializer,
        ),
        "update": ActionConfig(
            request=NavigationMenuSerializer,
            response=NavigationMenuSerializer,
        ),
        "partial_update": ActionConfig(
            request=NavigationMenuSerializer,
            response=NavigationMenuSerializer,
        ),
        "destroy": ActionConfig(response=NavigationMenuSerializer),
    }


class PageLayoutAdminViewSet(BaseModelViewSet):
    queryset = PageLayout.objects.prefetch_related("sections")
    # H22 (MULTI_TENANT_AUDIT.md) paired ``IsAdminUser`` with
    # ``HasTenantAccess`` so a platform-staff user could not mutate any
    # tenant's layout. That pairing was unsound on an API request:
    # ``UserTenantMembership.user`` is an FK to
    # ``public.user_useraccount``, but an API session authenticates
    # against the TENANT schema (knox is TENANT_APPS only), so the
    # membership lookup compared primary keys ACROSS schemas and
    # matched whichever public row happened to share the pk. It only
    # ever "worked" because the cutover copied users id-preserving.
    #
    # ``IsPlatformSuperuser`` closes H22 directly and soundly: is_staff
    # is no longer the gate at all.
    permission_classes = [IsPlatformSuperuser]
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
