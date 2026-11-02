"""The builder's HTTP surface.

Staff routes (``StoreStaffModelPermissions``: the role in this store
decides, so ADMIN and OWNER edit and publish and STAFF only look) and one
public read of the current release. All of it is a thin layer over
``builder.services`` (the only writer) and ``builder.published``.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from django.utils.cache import patch_cache_control
from django.utils.translation import gettext as _
from djangorestframework_camel_case.util import camel_to_underscore
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from builder import (
    link_picker,
    published,
    references,
    scheduling,
    services,
)
from builder.errors import (
    BuilderError,
    InvalidKind,
    InvalidLinkSearch,
    InvalidLinkTargets,
    NotFoundError,
    PreconditionRequired,
)
from builder.localization import (
    LOCALE_PARAM,
    requested_locale,
    resolve_locale,
)
from builder.models import (
    BuilderDocument,
    BuilderRelease,
    BuilderScheduledPublish,
    BuilderVersion,
    DocumentKind,
    ReferenceKind,
    ScheduleStatus,
)
from builder.rendering import (
    BuilderJSONParser,
    BuilderJSONRenderer,
    LinkLabelsRenderer,
    PreviewJSONParser,
)
from builder.serializers import (
    CheckpointWriteSerializer,
    DocumentDetailSerializer,
    DocumentListSerializer,
    DraftWriteSerializer,
    LinkLabelsResponseSerializer,
    LinkSearchResponseSerializer,
    LinkTargetsResponseSerializer,
    PreviewResolveSerializer,
    PublishWriteSerializer,
    ReleaseSerializer,
    RescheduleSerializer,
    RollbackWriteSerializer,
    ScheduleSerializer,
    ScheduleWriteSerializer,
    VersionDetailSerializer,
    VersionListSerializer,
)
from core.api.permissions import IsStoreStaff, StoreStaffModelPermissions
from core.api.serializers import ErrorResponseSerializer

TAGS = ["Builder"]
LOCALE_OPENAPI_PARAMETER = OpenApiParameter(
    name=LOCALE_PARAM,
    location=OpenApiParameter.QUERY,
    required=False,
    type=str,
    description=(
        "Language to resolve the content in; unknown or omitted means the "
        "store's default language."
    ),
)
ERRORS = {
    400: OpenApiResponse(response=ErrorResponseSerializer),
    409: OpenApiResponse(response=ErrorResponseSerializer),
}


def expected_revision(request: Request) -> int:
    """The revision named in ``If-Match`` (``"3"``, ``W/"3"`` or ``3``)."""
    raw = request.headers.get("If-Match")
    if raw is None:
        raise PreconditionRequired()
    value = raw.strip().removeprefix("W/").strip('"')
    if not value.isdigit():
        raise BuilderError(
            _("If-Match must be a revision number."), code="invalid_revision"
        )
    return int(value)


def _etagged(response: Response, document: BuilderDocument) -> Response:
    response["ETag"] = f'"{document.draft_revision}"'
    return response


class StaffView(APIView):
    """Common to every staff route."""

    renderer_classes = [BuilderJSONRenderer]
    parser_classes = [BuilderJSONParser]
    permission_classes = [StoreStaffModelPermissions]
    queryset: QuerySet[Any] = BuilderDocument.objects.none()


class StaffListView(ListAPIView):
    renderer_classes = [BuilderJSONRenderer]
    permission_classes = [StoreStaffModelPermissions]


_IF_MATCH = OpenApiParameter(
    name="If-Match",
    location=OpenApiParameter.HEADER,
    required=True,
    type=str,
    description=(
        "The draft revision this change is made against, as read from the "
        "document (`draftRevision`, or the ETag). `0` creates a document "
        "that does not exist. A stale revision answers 409 "
        "`revision_conflict` with the current one and who saved it."
    ),
)


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
    @extend_schema(tags=TAGS, responses=DocumentDetailSerializer)
    def get(self, request: Request, key: str) -> Response:
        document = get_object_or_404(BuilderDocument, key=key)
        return _etagged(
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
        parameters=[_IF_MATCH],
        responses={204: None, **ERRORS},
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
        parameters=[_IF_MATCH],
        request=DraftWriteSerializer,
        responses={200: DocumentDetailSerializer, **ERRORS},
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
        return _etagged(
            Response(DocumentDetailSerializer(document).data), document
        )


class DiscardView(StaffView):
    @extend_schema(
        tags=TAGS,
        parameters=[_IF_MATCH],
        request=None,
        responses={200: DocumentDetailSerializer, **ERRORS},
    )
    def post(self, request: Request, key: str) -> Response:
        document = services.discard_draft(
            request.user, key=key, expected_revision=expected_revision(request)
        )
        return _etagged(
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
        responses={201: VersionListSerializer, **ERRORS},
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

    @extend_schema(tags=TAGS, responses=VersionDetailSerializer)
    def get(self, request: Request, key: str, number: int) -> Response:
        version = get_object_or_404(
            BuilderVersion, document__key=key, number=number
        )
        return Response(VersionDetailSerializer(version).data)


class RestoreView(StaffView):
    @extend_schema(
        tags=TAGS,
        parameters=[_IF_MATCH],
        request=None,
        responses={200: DocumentDetailSerializer, **ERRORS},
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
        return _etagged(
            Response(DocumentDetailSerializer(document).data), document
        )


class PublishView(StaffView):
    queryset = BuilderRelease.objects.none()

    @extend_schema(
        tags=TAGS,
        request=PublishWriteSerializer,
        responses={201: ReleaseSerializer, **ERRORS},
        description=(
            "Release the drafts of the named documents together, or none "
            "of them. Every draft is validated in full, references "
            "included; the answer lists every problem with its `document`."
        ),
    )
    def post(self, request: Request) -> Response:
        body = PublishWriteSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        result = services.publish(
            request.user,
            keys=body.validated_data["documents"],
            note=body.validated_data["note"],
        )
        return Response(
            ReleaseSerializer(result.release).data,
            status=status.HTTP_201_CREATED,
        )


class RollbackView(StaffView):
    queryset = BuilderRelease.objects.none()

    @extend_schema(
        tags=TAGS,
        request=RollbackWriteSerializer,
        responses={201: ReleaseSerializer, **ERRORS},
        description=("A new release pointing at the versions of an older one."),
    )
    def post(self, request: Request) -> Response:
        body = RollbackWriteSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        release = services.rollback(
            request.user,
            release_id=body.validated_data["release"],
            note=body.validated_data["note"],
        )
        return Response(
            ReleaseSerializer(release).data, status=status.HTTP_201_CREATED
        )


@extend_schema(tags=TAGS)
class ReleaseListView(StaffListView):
    queryset = BuilderRelease.objects.all()
    serializer_class = ReleaseSerializer

    def get_queryset(self) -> QuerySet[BuilderRelease]:
        return BuilderRelease.objects.select_related(
            "rolled_back_from"
        ).prefetch_related("published_by_staff")


@extend_schema(tags=TAGS)
class ScheduleListView(StaffListView):
    """Scheduled publishes, soonest first; ``?status=`` narrows."""

    queryset = BuilderScheduledPublish.objects.all()
    serializer_class = ScheduleSerializer

    def get_queryset(self) -> QuerySet[BuilderScheduledPublish]:
        queryset = BuilderScheduledPublish.objects.select_related(
            "release"
        ).prefetch_related("created_by_staff")
        wanted = self.request.query_params.get("status")
        if wanted in ScheduleStatus.values:
            queryset = queryset.filter(status=wanted)
        return queryset

    @extend_schema(
        tags=TAGS,
        request=ScheduleWriteSerializer,
        responses={201: ScheduleSerializer, **ERRORS},
        description=(
            "Release the drafts of the named documents together at "
            "`scheduledFor` (an ISO 8601 time; one without an offset is read "
            "in the store's timezone). Validated in full now, references "
            "included, and frozen: what goes live is what was checked."
        ),
    )
    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        body = ScheduleWriteSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        schedule = scheduling.schedule_publish(
            request.user,
            keys=body.validated_data["documents"],
            when=body.validated_data["scheduled_for"],
            note=body.validated_data["note"],
        )
        return Response(
            ScheduleSerializer(schedule).data, status=status.HTTP_201_CREATED
        )


class ScheduleView(StaffView):
    queryset = BuilderScheduledPublish.objects.none()

    @extend_schema(tags=TAGS, responses=ScheduleSerializer)
    def get(self, request: Request, pk: int) -> Response:
        return Response(
            ScheduleSerializer(
                get_object_or_404(BuilderScheduledPublish, pk=pk)
            ).data
        )

    @extend_schema(
        tags=TAGS,
        request=RescheduleSerializer,
        responses={200: ScheduleSerializer, **ERRORS},
        description="Move a pending schedule; 409 `schedule_not_pending` otherwise.",
    )
    def patch(self, request: Request, pk: int) -> Response:
        body = RescheduleSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        schedule = scheduling.reschedule(
            request.user, pk=pk, when=body.validated_data["scheduled_for"]
        )
        return Response(ScheduleSerializer(schedule).data)

    @extend_schema(
        tags=TAGS,
        responses={200: ScheduleSerializer, **ERRORS},
        description=(
            "Cancel a pending schedule (it stays listed as `cancelled`); "
            "409 `schedule_not_pending` once it has run."
        ),
    )
    def delete(self, request: Request, pk: int) -> Response:
        return Response(
            ScheduleSerializer(scheduling.cancel(request.user, pk=pk)).data
        )


class ScheduleAcknowledgeView(StaffView):
    queryset = BuilderScheduledPublish.objects.none()

    @extend_schema(
        tags=TAGS,
        request=None,
        responses={200: ScheduleSerializer, **ERRORS},
        description=(
            "Say that a failed schedule was seen; it stops showing on the "
            "store dashboard. Repeating it changes nothing."
        ),
    )
    def post(self, request: Request, pk: int) -> Response:
        return Response(
            ScheduleSerializer(scheduling.acknowledge(request.user, pk=pk)).data
        )


class PublishedIndexView(APIView):
    """The current release's theme, groups and page list, for one locale."""

    renderer_classes = [BuilderJSONRenderer]
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="builder_published_index",
        tags=TAGS,
        parameters=[LOCALE_OPENAPI_PARAMETER],
        responses={200: dict},
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
            raw = request.query_params.get(camel_to_underscore(kind.value))
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
            200: dict,
            400: OpenApiResponse(ErrorResponseSerializer),
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
            raw = request.query_params.get(camel_to_underscore(kind.value))
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


class PublishedPageView(APIView):
    """The current release's theme, groups and page, for one locale."""

    renderer_classes = [BuilderJSONRenderer]
    permission_classes = [AllowAny]

    @extend_schema(
        tags=TAGS,
        parameters=[LOCALE_OPENAPI_PARAMETER],
        responses={200: dict, 404: OpenApiResponse(ErrorResponseSerializer)},
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
