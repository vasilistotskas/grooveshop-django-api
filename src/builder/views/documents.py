"""Documents, their drafts and their stored versions (staff routes)."""

from __future__ import annotations

from typing import Any

from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from builder import (
    services,
)
from builder.errors import (
    InvalidKind,
)
from builder.models import (
    BuilderDocument,
    BuilderVersion,
    DocumentKind,
)
from builder.schema import (
    DOCUMENT_INVALID_ERRORS,
    ERRORS,
    IF_MATCH,
    NOT_FOUND,
    REVISION_ERRORS,
    STORE_UNAVAILABLE,
    TAGS,
)
from builder.serializers import (
    CheckpointWriteSerializer,
    DocumentDetailSerializer,
    DocumentListSerializer,
    DraftWriteSerializer,
    VersionDetailSerializer,
    VersionListSerializer,
)
from builder.views.base import (
    StaffListView,
    StaffView,
    etagged,
    expected_revision,
)
from core.api.serializers import ErrorResponseSerializer


@extend_schema(
    tags=TAGS,
    parameters=[
        OpenApiParameter(
            name="kind",
            location=OpenApiParameter.QUERY,
            required=False,
            type=OpenApiTypes.STR,
            enum=DocumentKind.values,
            description="Only the documents of this kind.",
        )
    ],
    responses={
        200: DocumentListSerializer(many=True),
        400: OpenApiResponse(response=ErrorResponseSerializer),
    },
)
class DocumentListView(StaffListView):
    queryset = BuilderDocument.objects.all()
    serializer_class = DocumentListSerializer

    def get_queryset(self) -> QuerySet[BuilderDocument]:
        queryset = BuilderDocument.objects.prefetch_related("updated_by_staff")
        kind = self.request.query_params.get("kind")
        if kind is not None:
            if kind not in DocumentKind.values:
                raise InvalidKind(
                    _("kind must be one of: %(kinds)s.")
                    % {"kinds": ", ".join(DocumentKind.values)},
                    param="kind",
                )
            queryset = queryset.filter(kind=kind)
        return queryset.order_by("key")

    def get_serializer_context(self) -> dict[str, Any]:
        return {
            **super().get_serializer_context(),
            "published_numbers": services.published_numbers(),
        }


class DocumentView(StaffView):
    @extend_schema(
        tags=TAGS, responses={200: DocumentDetailSerializer, **NOT_FOUND}
    )
    def get(self, request: Request, key: str) -> Response:
        document = get_object_or_404(BuilderDocument, key=key)
        return etagged(
            Response(
                DocumentDetailSerializer(
                    document,
                    context={"published_numbers": services.published_numbers()},
                ).data
            ),
            document,
        )

    @extend_schema(
        tags=TAGS,
        parameters=[IF_MATCH],
        responses={
            204: None,
            **DOCUMENT_INVALID_ERRORS,
            **NOT_FOUND,
            **REVISION_ERRORS,
        },
        description="Delete a page. A released page leaves the store with a "
        "new release; a page another released document links to is refused "
        "(`page_in_use`).",
    )
    def delete(self, request: Request, key: str) -> Response:
        services.delete_page(
            request.user, key=key, expected_revision=expected_revision(request)
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class DraftView(StaffView):
    @extend_schema(
        tags=TAGS,
        parameters=[IF_MATCH],
        request=DraftWriteSerializer,
        responses={
            200: DocumentDetailSerializer,
            **DOCUMENT_INVALID_ERRORS,
            **NOT_FOUND,
            **REVISION_ERRORS,
            **STORE_UNAVAILABLE,
        },
        description=(
            "Autosave. The document is validated in full against the "
            "storefront's catalogue; the answer is 503 "
            "`storefront_unavailable` when it cannot be checked, and 400 "
            "`invalid_document` with every problem as `{path, code, "
            "message}` (a JSON pointer into what was sent)."
        ),
    )
    def put(self, request: Request, key: str) -> Response:
        body = DraftWriteSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        document = services.save_draft(
            request.user,
            key=key,
            draft=body.validated_data["draft"],
            i18n=body.validated_data["i18n"],
            expected_revision=expected_revision(request),
        )
        return etagged(
            Response(DocumentDetailSerializer(document).data), document
        )


class DiscardView(StaffView):
    @extend_schema(
        tags=TAGS,
        parameters=[IF_MATCH],
        request=None,
        responses={
            200: DocumentDetailSerializer,
            **ERRORS,
            **NOT_FOUND,
            **REVISION_ERRORS,
        },
    )
    def post(self, request: Request, key: str) -> Response:
        document = services.discard_draft(
            request.user, key=key, expected_revision=expected_revision(request)
        )
        return etagged(
            Response(DocumentDetailSerializer(document).data), document
        )


class VersionListView(StaffListView):
    serializer_class = VersionListSerializer
    queryset = BuilderVersion.objects.all()

    def get_queryset(self) -> QuerySet[BuilderVersion]:
        document = get_object_or_404(BuilderDocument, key=self.kwargs["key"])
        return document.versions.prefetch_related("created_by_staff").order_by(
            "-number"
        )

    @extend_schema(tags=TAGS)
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return super().get(request, *args, **kwargs)


class CheckpointView(StaffView):
    queryset = BuilderVersion.objects.none()

    @extend_schema(
        tags=TAGS,
        request=CheckpointWriteSerializer,
        responses={
            201: VersionListSerializer,
            **DOCUMENT_INVALID_ERRORS,
            **NOT_FOUND,
            **STORE_UNAVAILABLE,
        },
        description="A named save: freeze the current draft as a version.",
    )
    def post(self, request: Request, key: str) -> Response:
        body = CheckpointWriteSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        version = services.create_checkpoint(
            request.user, key=key, note=body.validated_data["note"]
        )
        return Response(
            VersionListSerializer(version).data, status=status.HTTP_201_CREATED
        )


class VersionView(StaffView):
    queryset = BuilderVersion.objects.none()

    @extend_schema(
        tags=TAGS, responses={200: VersionDetailSerializer, **NOT_FOUND}
    )
    def get(self, request: Request, key: str, number: int) -> Response:
        version = get_object_or_404(
            BuilderVersion, document__key=key, number=number
        )
        return Response(VersionDetailSerializer(version).data)


class RestoreView(StaffView):
    @extend_schema(
        tags=TAGS,
        parameters=[IF_MATCH],
        request=None,
        responses={
            200: DocumentDetailSerializer,
            **ERRORS,
            **NOT_FOUND,
            **REVISION_ERRORS,
        },
        description=(
            "Put a version back as the DRAFT. It is not checked against "
            "the catalogue (the editor upgrades old blocks); publishing "
            "validates."
        ),
    )
    def post(self, request: Request, key: str, number: int) -> Response:
        document = services.restore_version(
            request.user,
            key=key,
            number=number,
            expected_revision=expected_revision(request),
        )
        return etagged(
            Response(DocumentDetailSerializer(document).data), document
        )
