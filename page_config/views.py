from __future__ import annotations

from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from core.api.permissions import StoreStaffModelPermissions
from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.utils.serializers import (
    ActionConfig,
    SerializersConfig,
    create_schema_view_config,
    crud_config,
)
from page_config.models import (
    ContentPage,
    NavigationMenu,
    PageLayout,
    PageSection,
)
from page_config.serializers import (
    ContentPageDetailSerializer,
    ContentPageSerializer,
    ContentPageWriteSerializer,
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
    # Role-derived — same rationale as PageLayoutAdminViewSet below.
    permission_classes = [StoreStaffModelPermissions]
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
    # H22 (MULTI_TENANT_AUDIT.md): a platform-staff user must not be
    # able to mutate ANOTHER tenant's layout. The original fix paired
    # ``IsAdminUser`` with ``HasTenantAccess``, which was unsound on an
    # API request (the membership lookup compared primary keys ACROSS
    # schemas — see docs/api-staff-identity.md); the interim fix locked
    # these routes to platform superusers.
    #
    # ``StoreStaffModelPermissions`` is the end state: the permission
    # set is derived from the caller's ROLE in the tenant currently on
    # the connection, so an OWNER of store A holds nothing when the
    # request arrives on store B's host — H22 by construction, and
    # is_staff is not the gate at all.
    permission_classes = [StoreStaffModelPermissions]
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


content_page_serializers_config: SerializersConfig = crud_config(
    list=ContentPageSerializer,
    detail=ContentPageDetailSerializer,
    write=ContentPageWriteSerializer,
)


@extend_schema_view(
    **create_schema_view_config(
        model_class=ContentPage,
        display_config={"tag": "Content Page"},
        serializers_config=content_page_serializers_config,
        error_serializer=ErrorResponseSerializer,
    )
)
class ContentPageViewSet(BaseModelViewSet):
    """Merchant-editable store-policy pages, looked up by slug.

    Public reads (AllowAny) only ever see published pages; writes are
    staff-only (``StoreStaffModelPermissions``) — same split as
    ``BlogPostViewSet``. A plain ``IsAuthenticatedOrReadOnly`` would let
    any signed-in customer rewrite the store's Terms/Privacy page, so
    this deliberately does not use the project's blanket default.
    """

    queryset = ContentPage.objects.all()
    serializers_config = content_page_serializers_config
    lookup_field = "slug"

    ordering_fields = ["slug", "created_at", "updated_at", "published_at"]
    ordering = ["slug"]
    search_fields = ["translations__title", "translations__body", "slug"]

    def get_permissions(self):
        if self.action in (
            "create",
            "update",
            "partial_update",
            "destroy",
        ):
            return [StoreStaffModelPermissions()]
        return [AllowAny()]

    def get_queryset(self):
        if self.action == "list":
            queryset = ContentPage.objects.for_list()
        else:
            queryset = ContentPage.objects.for_detail()

        # Unpublished pages (drafts / not-yet-reviewed placeholders) must
        # never be exposed to the public; only staff may see them. Reads
        # are AllowAny, so without this filter anonymous callers could
        # enumerate unpublished pages by slug — mirrors
        # ``BlogPostViewSet.get_queryset``.
        user = self.request.user
        if not (user and user.is_authenticated and user.is_staff):
            queryset = queryset.published()

        return queryset
