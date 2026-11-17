"""The public reads of the current release: no sign-in, no draft."""

from __future__ import annotations

from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
)
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from builder import (
    published,
)
from builder.errors import (
    NotFoundError,
)
from builder.localization import (
    requested_locale,
)
from builder.rendering import (
    BuilderJSONRenderer,
)
from builder.schema import LOCALE_OPENAPI_PARAMETER, TAGS
from builder.serializers import (
    PublishedIndexSerializer,
    PublishedPageSerializer,
)
from core.api.serializers import ErrorResponseSerializer


class PublishedIndexView(APIView):
    """The current release's theme, groups and page list, for one locale."""

    renderer_classes = [BuilderJSONRenderer]
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="builder_published_index",
        tags=TAGS,
        parameters=[LOCALE_OPENAPI_PARAMETER],
        responses={200: PublishedIndexSerializer},
        description=(
            "`{release, locale, theme, groups, pages}`: what every page "
            "needs and none owns. `theme` and `groups` are resolved like "
            "the page read's; `pages` lists every `page:` and `template:` "
            "document of the CURRENT release as `{key, kind, updatedAt}` "
            "(`key` is the suffix, `updatedAt` when its version was "
            "made). A store with no release answers `{release: 0, theme: "
            "null, groups: {}, pages: []}`, not 404. Never reads a draft "
            "and never calls the storefront."
        ),
    )
    def get(self, request: Request) -> Response:
        return Response(published.published_index(requested_locale(request)))


class PublishedPageView(APIView):
    """The current release's theme, groups and page, for one locale."""

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
            "`{release, locale, theme, groups, page}`: the documents of "
            "the CURRENT release resolved for the locale (overlay merged, "
            "disabled nodes dropped). `page` is the page of that key, else "
            "the template of that name. Never reads a draft and never "
            "calls the storefront; the content keys are the storefront's "
            "own and are not camelCased."
        ),
    )
    def get(self, request: Request, page_key: str) -> Response:
        payload = published.published_page(page_key, requested_locale(request))
        if payload is None:
            raise NotFoundError(code="page_not_published")
        return Response(payload)
