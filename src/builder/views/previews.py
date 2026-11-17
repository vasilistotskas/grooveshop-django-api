"""Previews: the editor's live preview, share-preview links and the public
read behind one.
"""

from __future__ import annotations

from django.utils.cache import patch_cache_control
from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
)
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from builder import (
    preview_links,
    services,
)
from builder.errors import (
    NotFoundError,
)
from builder.localization import (
    requested_locale,
    resolve_locale,
)
from builder.models import (
    BuilderPreviewLink,
)
from builder.rendering import (
    BuilderJSONRenderer,
    PreviewJSONParser,
)
from builder.schema import (
    DOCUMENT_INVALID_ERRORS,
    LOCALE_OPENAPI_PARAMETER,
    NOT_FOUND,
    STORE_UNAVAILABLE,
    TAGS,
)
from builder.serializers import (
    BuilderDocumentInvalidErrorSerializer,
    PreviewLinkCreatedSerializer,
    PreviewLinkSerializer,
    PreviewLinkWriteSerializer,
    PreviewResolveSerializer,
    PublishedPageSerializer,
)
from builder.views.base import StaffView
from core.api.permissions import IsStoreStaff
from core.api.serializers import ErrorResponseSerializer


class PreviewResolveView(StaffView):
    """The editor's live preview: unsaved working copies, resolved the way
    the public read resolves a release."""

    parser_classes = [PreviewJSONParser]
    # A POST that stores nothing: ``StoreStaffModelPermissions`` would ask
    # for the ADD permission, so the service asks for VIEW instead.
    permission_classes = [IsStoreStaff]

    @extend_schema(
        tags=TAGS,
        request=PreviewResolveSerializer,
        responses={
            200: PublishedPageSerializer,
            400: OpenApiResponse(BuilderDocumentInvalidErrorSerializer),
            404: OpenApiResponse(ErrorResponseSerializer),
            503: OpenApiResponse(ErrorResponseSerializer),
        },
        description=(
            "`{release, locale, theme, groups, page}`, as `published/"
            "<pageKey>` answers, if the working copies in `documents` "
            "(`{key: {draft, i18n}}`, at most `BUILDER_PREVIEW_MAX_"
            "DOCUMENTS`) were released now: each stands in for the current "
            "release's document of its key and everything else is the "
            "release's, resolved by the same code (overlay merged, "
            "disabled nodes dropped). A working copy has `version: 0`. "
            "Every copy is validated like a draft save (400 "
            "`invalid_document`, each problem with its `document`; "
            "references are not checked, as on a save); nothing is stored. "
            "404 `page_not_published` when neither a copy nor the release "
            "has the page. Never cached: `Cache-Control: private, "
            "no-store`. The content keys are the storefront's own and are "
            "not camelCased."
        ),
    )
    def post(self, request: Request) -> Response:
        body = PreviewResolveSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        payload = services.preview(
            request.user,
            page_key=data["page_key"],
            locale=resolve_locale(data.get("locale")),
            copies={
                key: (copy["draft"], copy["i18n"])
                for key, copy in data["documents"].items()
            },
        )
        if payload is None:
            raise NotFoundError(code="page_not_published")
        response = Response(payload)
        patch_cache_control(response, private=True, no_store=True)
        return response


class PreviewLinkListView(StaffView):
    """Share-preview links: make one, list the open ones."""

    queryset = BuilderPreviewLink.objects.none()

    @extend_schema(
        tags=TAGS,
        responses={200: PreviewLinkSerializer(many=True)},
        description="The links that still open, newest first.",
    )
    def get(self, request: Request) -> Response:
        return Response(
            PreviewLinkSerializer(
                preview_links.active().prefetch_related("created_by_staff"),
                many=True,
            ).data
        )

    @extend_schema(
        tags=TAGS,
        request=PreviewLinkWriteSerializer,
        responses={
            201: PreviewLinkCreatedSerializer,
            **DOCUMENT_INVALID_ERRORS,
            **NOT_FOUND,
            **STORE_UNAVAILABLE,
        },
        description=(
            "Freeze the current drafts of the named documents behind a link "
            "anyone can open without signing in, for "
            "`BUILDER_PREVIEW_LINK_TTL_SECONDS`. Every draft is validated "
            "like a save (400 `invalid_document` with each problem's "
            "`document`). The `token` is in this answer only; open "
            "`published`-style at `preview/<token>/<pageKey>`."
        ),
    )
    def post(self, request: Request) -> Response:
        body = PreviewLinkWriteSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        link, token = preview_links.create(
            request.user,
            keys=body.validated_data["documents"],
            note=body.validated_data["note"],
        )
        link.token = token
        return Response(
            PreviewLinkCreatedSerializer(link).data,
            status=status.HTTP_201_CREATED,
        )


class PreviewLinkView(StaffView):
    queryset = BuilderPreviewLink.objects.none()

    @extend_schema(
        tags=TAGS,
        request=None,
        responses={204: None, 404: OpenApiResponse(ErrorResponseSerializer)},
        description="Revoke a link at once.",
    )
    def delete(self, request: Request, pk: int) -> Response:
        preview_links.revoke(request.user, pk=pk)
        return Response(status=status.HTTP_204_NO_CONTENT)


class SharedPreviewView(APIView):
    """What a share-preview link shows: the page with the shared drafts
    over the current release. The token is the credential."""

    renderer_classes = [BuilderJSONRenderer]
    permission_classes = [AllowAny]

    @extend_schema(
        tags=TAGS,
        parameters=[LOCALE_OPENAPI_PARAMETER],
        responses={
            200: PublishedPageSerializer,
            404: OpenApiResponse(ErrorResponseSerializer),
        },
        description=(
            "The shape of `published/<pageKey>`, with the documents frozen in "
            "the link standing in for the current release's. 404 "
            "`preview_link_expired` for a token that is unknown, revoked or "
            "past its time, `page_not_published` when neither the link nor "
            "the release has the page. Never cached: `Cache-Control: "
            "private, no-store`, and not for search engines."
        ),
    )
    def get(self, request: Request, token: str, page_key: str) -> Response:
        payload = preview_links.open_link(
            token, page_key, requested_locale(request)
        )
        if payload is None:
            raise NotFoundError(code="page_not_published")
        response = Response(payload)
        patch_cache_control(response, private=True, no_store=True)
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response
