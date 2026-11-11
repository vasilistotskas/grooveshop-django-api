from __future__ import annotations

from drf_spectacular.utils import extend_schema_view
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny

from core.api.permissions import StoreStaffModelPermissions
from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.utils.serializers import (
    SerializersConfig,
    create_schema_view_config,
    crud_config,
)
from page_config.legal_guard import NOT_DELETABLE, is_legal_page
from page_config.models import ContentPage
from page_config.serializers import (
    ContentPageDetailSerializer,
    ContentPageSerializer,
    ContentPageWriteSerializer,
)
from tenant.membership import is_store_staff

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

    def perform_destroy(self, instance):
        # A legal page is never deleted (page_config.legal_guard).
        if is_legal_page(instance.slug):
            raise ValidationError(
                {"slug": NOT_DELETABLE}, code="legal_page_not_deletable"
            )
        super().perform_destroy(instance)

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
        if not is_store_staff(self.request.user):
            queryset = queryset.published()

        return queryset
