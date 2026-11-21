"""What a link points at: the slugs a storefront builds paths from, the
link picker's search and the labels of stored links.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.utils.translation import gettext as _
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from builder import (
    link_picker,
    references,
)
from builder.errors import (
    InvalidLinkSearch,
    InvalidLinkTargets,
)
from builder.localization import (
    requested_locale,
)
from builder.models import (
    ReferenceKind,
)
from builder.rendering import (
    BuilderJSONRenderer,
    LinkLabelsRenderer,
)
from builder.schema import LOCALE_OPENAPI_PARAMETER, TAGS
from builder.serializers import (
    LinkLabelsResponseSerializer,
    LinkSearchResponseSerializer,
    LinkTargetsResponseSerializer,
)
from builder.views.base import StaffView
from core.api.serializers import ErrorResponseSerializer
from core.utils.string_case import camel_to_snake


def _parse_ids(raw: str) -> set[int] | None:
    """The positive ids of a comma-separated list, or ``None`` when it has
    something else in it or more ids than a call may resolve."""
    try:
        ids = {int(part) for part in raw.split(",") if part}
    except ValueError:
        return None
    if len(ids) > settings.BUILDER_LINK_TARGETS_MAX_IDS or not all(
        0 < pk <= references.MAX_ID for pk in ids
    ):
        return None
    return ids


def _parse_keys(kind: ReferenceKind, raw: str) -> set[str] | None:
    """The page keys or content page slugs of a comma-separated list, or
    ``None`` when one is not a valid name or there are too many."""
    keys = {part for part in raw.split(",") if part}
    if len(keys) > settings.BUILDER_LINK_TARGETS_MAX_IDS or not all(
        references.accepts_value(kind, key) for key in keys
    ):
        return None
    return keys


class LinkTargetsView(APIView):
    """The slug of each product, category, post and blog category a link
    points at, for the storefront to build the path."""

    renderer_classes = [BuilderJSONRenderer]
    permission_classes = [AllowAny]

    @extend_schema(
        tags=TAGS,
        parameters=[
            OpenApiParameter(
                name=kind.value,
                location=OpenApiParameter.QUERY,
                required=False,
                type=str,
                description=(
                    f"Comma-separated {kind.label.lower()} ids to resolve "
                    "(at most `BUILDER_LINK_TARGETS_MAX_IDS`)."
                ),
            )
            for kind in references.LINK_TARGET_KINDS
        ],
        responses={
            200: LinkTargetsResponseSerializer,
            400: OpenApiResponse(ErrorResponseSerializer),
        },
        description=(
            "`{kind: {id: slug}}` for the kinds asked about. An id that is "
            "missing, inactive or unpublished is absent, by the rules a "
            "publish checks references with. Slugs are the same in every "
            "language. Reads no draft and never calls the storefront."
        ),
    )
    def get(self, request: Request) -> Response:
        return Response(references.link_targets(self._ids(request)))

    @staticmethod
    def _ids(request: Request) -> dict[ReferenceKind, set[int]]:
        ids: dict[ReferenceKind, set[int]] = {}
        for kind in references.LINK_TARGET_KINDS:
            # The camel-case middleware has already underscored the query
            # keys: a client's ``blogPost`` arrives as ``blog_post``.
            raw = request.query_params.get(camel_to_snake(kind.value))
            if raw is None:
                continue
            wanted = _parse_ids(raw)
            if wanted is None:
                raise InvalidLinkTargets(
                    _("%(kind)s must be at most %(limit)s positive ids.")
                    % {
                        "kind": kind.value,
                        "limit": settings.BUILDER_LINK_TARGETS_MAX_IDS,
                    },
                    param=kind.value,
                )
            ids[kind] = wanted
        return ids


class LinkSearchView(StaffView):
    """The targets a link can point at that match what the person typed."""

    @extend_schema(
        tags=TAGS,
        parameters=[
            OpenApiParameter(
                name="q",
                location=OpenApiParameter.QUERY,
                required=False,
                type=str,
                description=(
                    "Text the label or slug contains. Shorter than "
                    "`BUILDER_LINK_SEARCH_MIN_CHARS` (2 by default) "
                    "answers an empty list, not an error."
                ),
            ),
            OpenApiParameter(
                name="kinds",
                location=OpenApiParameter.QUERY,
                required=False,
                type=str,
                description=(
                    "Comma-separated kinds to search, in the order of the "
                    "answer: "
                    + ", ".join(k.value for k in link_picker.PICKER_KINDS)
                    + ". All when omitted."
                ),
            ),
            LOCALE_OPENAPI_PARAMETER,
        ],
        responses={
            200: LinkSearchResponseSerializer,
            400: OpenApiResponse(ErrorResponseSerializer),
        },
        description=(
            "`{results: [{kind, id, label, slug, active}]}`, grouped by "
            "kind and ordered by label, at most `BUILDER_LINK_SEARCH_LIMIT` "
            "of each kind. Only what a link may point at (the rule a "
            "publish checks references with), so `active` is always true. "
            "`id` is a number for products, categories, posts and blog "
            "categories and the key or slug for pages and content pages; "
            "a page result also has `source` (`page` or `template`). The "
            "label is in `locale`, else the default language, else the "
            "slug. The storefront builds the path from the slug."
        ),
    )
    def get(self, request: Request) -> Response:
        return Response(
            {
                "results": link_picker.search(
                    request.query_params.get("q", ""),
                    self._kinds(request),
                    requested_locale(request),
                )
            }
        )

    @staticmethod
    def _kinds(request: Request) -> list[ReferenceKind]:
        raw = request.query_params.get("kinds")
        if not raw:
            return list(link_picker.PICKER_KINDS)
        kinds: list[ReferenceKind] = []
        for part in raw.split(","):
            if part not in link_picker.PICKER_KINDS:
                raise InvalidLinkSearch(
                    _("kinds must be a comma-separated list of: %(kinds)s.")
                    % {
                        "kinds": ", ".join(
                            k.value for k in link_picker.PICKER_KINDS
                        )
                    },
                    param="kinds",
                )
            if (kind := ReferenceKind(part)) not in kinds:
                kinds.append(kind)
        return kinds


class LinkLabelsView(StaffView):
    """The name of each target a stored link points at, found or not."""

    renderer_classes = [LinkLabelsRenderer]

    @extend_schema(
        tags=TAGS,
        parameters=[
            *(
                OpenApiParameter(
                    name=kind.value,
                    location=OpenApiParameter.QUERY,
                    required=False,
                    type=str,
                    description=(
                        "Comma-separated "
                        + (
                            "ids"
                            if kind in link_picker.NUMERIC_KINDS
                            else "keys (page keys, content page slugs)"
                        )
                        + " (at most `BUILDER_LINK_TARGETS_MAX_IDS`)."
                    ),
                )
                for kind in link_picker.PICKER_KINDS
            ),
            LOCALE_OPENAPI_PARAMETER,
        ],
        responses={
            200: LinkLabelsResponseSerializer,
            400: OpenApiResponse(ErrorResponseSerializer),
        },
        description=(
            "`{kind: {id: {label, active}}}` for the ids asked about, "
            "whether or not they can still be linked: `active` is the rule "
            "a publish checks references with, so an inactive or "
            "unpublished record keeps its `label` with `active: false`, "
            "and one that exists nowhere is `{label: null, active: "
            "false}`. A page also has `source` (`page` or `template`). "
            "The label is in `locale`, else the default language."
        ),
    )
    def get(self, request: Request) -> Response:
        return Response(
            link_picker.labels(self._wanted(request), requested_locale(request))
        )

    @staticmethod
    def _wanted(request: Request) -> dict[ReferenceKind, set[Any]]:
        wanted: dict[ReferenceKind, set[Any]] = {}
        for kind in link_picker.PICKER_KINDS:
            # Underscored by the camel-case middleware, like link-targets.
            raw = request.query_params.get(camel_to_snake(kind.value))
            if raw is None:
                continue
            ids = (
                _parse_ids(raw)
                if kind in link_picker.NUMERIC_KINDS
                else _parse_keys(kind, raw)
            )
            if ids is None:
                raise InvalidLinkTargets(
                    _("%(kind)s must be at most %(limit)s valid ids.")
                    % {
                        "kind": kind.value,
                        "limit": settings.BUILDER_LINK_TARGETS_MAX_IDS,
                    },
                    param=kind.value,
                )
            wanted[kind] = ids
        return wanted
