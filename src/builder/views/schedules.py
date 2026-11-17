"""Scheduled publishes (staff routes)."""

from __future__ import annotations

from typing import Any

from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
)
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from builder import (
    scheduling,
)
from builder.models import (
    BuilderScheduledPublish,
    ScheduleStatus,
)
from builder.schema import (
    DOCUMENT_INVALID_ERRORS,
    ERRORS,
    NOT_FOUND,
    STORE_UNAVAILABLE,
    TAGS,
)
from builder.serializers import (
    RescheduleSerializer,
    ScheduleSerializer,
    ScheduleWriteSerializer,
)
from builder.views.base import StaffListView, StaffView


@extend_schema(
    tags=TAGS,
    parameters=[
        OpenApiParameter(
            name="status",
            location=OpenApiParameter.QUERY,
            required=False,
            type=str,
            enum=ScheduleStatus.values,
            description=(
                "Only the schedules in this state; any other value lists "
                "them all."
            ),
        )
    ],
)
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
        responses={
            201: ScheduleSerializer,
            **DOCUMENT_INVALID_ERRORS,
            **NOT_FOUND,
            **STORE_UNAVAILABLE,
        },
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

    @extend_schema(tags=TAGS, responses={200: ScheduleSerializer, **NOT_FOUND})
    def get(self, request: Request, pk: int) -> Response:
        return Response(
            ScheduleSerializer(
                get_object_or_404(BuilderScheduledPublish, pk=pk)
            ).data
        )

    @extend_schema(
        tags=TAGS,
        request=RescheduleSerializer,
        responses={
            200: ScheduleSerializer,
            **DOCUMENT_INVALID_ERRORS,
            **NOT_FOUND,
        },
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
        responses={200: ScheduleSerializer, **ERRORS, **NOT_FOUND},
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
        responses={200: ScheduleSerializer, **ERRORS, **NOT_FOUND},
        description=(
            "Say that a failed schedule was seen; it stops showing on the "
            "store dashboard. Repeating it changes nothing."
        ),
    )
    def post(self, request: Request, pk: int) -> Response:
        return Response(
            ScheduleSerializer(scheduling.acknowledge(request.user, pk=pk)).data
        )
