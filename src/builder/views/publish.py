"""Publishing, checking a publish, rolling back and listing releases
(staff routes).
"""

from __future__ import annotations

from typing import Any, cast

from django.db.models import QuerySet
from drf_spectacular.utils import (
    extend_schema,
)
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from builder import (
    services,
)
from builder.models import (
    BuilderRelease,
)
from builder.schema import (
    DOCUMENT_INVALID_ERRORS,
    ERRORS,
    NOT_FOUND,
    STORE_UNAVAILABLE,
    TAGS,
)
from builder.serializers import (
    PublishCheckSerializer,
    PublishVersionWriteSerializer,
    PublishWriteSerializer,
    ReleaseSerializer,
    RollbackWriteSerializer,
)
from builder.views.base import StaffListView, StaffView


class PublishVersionView(StaffView):
    queryset = BuilderRelease.objects.none()

    @extend_schema(
        tags=TAGS,
        request=PublishVersionWriteSerializer,
        responses={
            201: ReleaseSerializer,
            **DOCUMENT_INVALID_ERRORS,
            **NOT_FOUND,
        },
        description=(
            "Release one stored version of ONE document: the previous "
            "release with only that key repointed. The draft is left as "
            "it is. Its references must still exist (`reference_not_found` "
            "with the `document`); `nothing_to_publish` when it is already "
            "the published version."
        ),
    )
    def post(self, request: Request, key: str, number: int) -> Response:
        body = PublishVersionWriteSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        release = services.publish_version(
            request.user,
            key=key,
            number=number,
            note=body.validated_data["note"],
        )
        return Response(
            ReleaseSerializer(release).data, status=status.HTTP_201_CREATED
        )


class PublishView(StaffView):
    queryset = BuilderRelease.objects.none()

    @extend_schema(
        tags=TAGS,
        request=PublishWriteSerializer,
        responses={
            201: ReleaseSerializer,
            **DOCUMENT_INVALID_ERRORS,
            **NOT_FOUND,
            **STORE_UNAVAILABLE,
        },
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


class PublishCheckView(StaffView):
    queryset = BuilderRelease.objects.none()

    @extend_schema(
        tags=TAGS,
        request=PublishWriteSerializer,
        responses={
            200: PublishCheckSerializer,
            **ERRORS,
            **NOT_FOUND,
            **STORE_UNAVAILABLE,
        },
        description=(
            "What `POST publish` of the same documents would do, without "
            "doing it: `ok`, the documents whose draft differs from the "
            "released version (`changed`), and every problem with its "
            "`document` (a draft that fails validation, a reference that "
            "no longer exists, `nothing_to_publish`). Same permission as "
            "the publish; stores nothing and takes no lock. An unknown "
            "document is `404`; no catalogue is `503`."
        ),
    )
    def post(self, request: Request) -> Response:
        body = PublishWriteSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        result = services.check_publish(
            request.user, keys=body.validated_data["documents"]
        )
        return Response(
            PublishCheckSerializer(
                {
                    "ok": result.ok,
                    "changed": list(result.changed),
                    "problems": [issue.as_dict() for issue in result.problems],
                }
            ).data
        )


class RollbackView(StaffView):
    queryset = BuilderRelease.objects.none()

    @extend_schema(
        tags=TAGS,
        request=RollbackWriteSerializer,
        responses={
            201: ReleaseSerializer,
            **DOCUMENT_INVALID_ERRORS,
            **NOT_FOUND,
        },
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

    def paginate_queryset(self, queryset: Any) -> Any:
        page = super().paginate_queryset(queryset)
        if page is not None:
            self._release_documents = services.release_documents(
                cast("list[BuilderRelease]", page)
            )
        return page

    def get_serializer_context(self) -> dict[str, Any]:
        return {
            **super().get_serializer_context(),
            "release_documents": getattr(self, "_release_documents", None),
        }
