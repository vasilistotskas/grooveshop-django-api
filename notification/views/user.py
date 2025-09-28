from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action

from rest_framework.response import Response

from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet

from core.utils.serializers import (
    create_schema_view_config,
    RequestSerializersConfig,
    ResponseSerializersConfig,
)
from core.utils.views import cache_methods
from notification.filters import NotificationUserFilter
from notification.models.user import NotificationUser
from notification.serializers.user import (
    NotificationCountResponseSerializer,
    NotificationSuccessResponseSerializer,
    NotificationUserActionSerializer,
    NotificationUserDetailSerializer,
    NotificationUserSerializer,
    NotificationUserWriteSerializer,
)

req_serializers: RequestSerializersConfig = {
    "create": NotificationUserWriteSerializer,
    "update": NotificationUserWriteSerializer,
    "partial_update": NotificationUserWriteSerializer,
    "mark_as_seen": NotificationUserActionSerializer,
    "mark_as_unseen": NotificationUserActionSerializer,
}

res_serializers: ResponseSerializersConfig = {
    "create": NotificationUserDetailSerializer,
    "list": NotificationUserSerializer,
    "retrieve": NotificationUserDetailSerializer,
    "update": NotificationUserDetailSerializer,
    "partial_update": NotificationUserDetailSerializer,
    "unseen_count": NotificationCountResponseSerializer,
    "mark_all_as_seen": NotificationSuccessResponseSerializer,
    "mark_all_as_unseen": NotificationSuccessResponseSerializer,
    "mark_as_seen": NotificationSuccessResponseSerializer,
    "mark_as_unseen": NotificationSuccessResponseSerializer,
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=NotificationUser,
        display_config={
            "tag": "Notification Users",
        },
        request_serializers=req_serializers,
        response_serializers=res_serializers,
    ),
    unseen_count=extend_schema(
        operation_id="getNotificationUserUnseenCount",
        summary=_("Get unseen notifications count"),
        description=_(
            "Get the count of unseen notifications for the authenticated user."
        ),
        tags=["Notification Users"],
        responses={
            200: NotificationCountResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
    mark_all_as_seen=extend_schema(
        operation_id="markAllNotificationUsersAsSeen",
        summary=_("Mark all notifications as seen"),
        description=_(
            "Mark all of the authenticated user's notifications as seen."
        ),
        tags=["Notification Users"],
        responses={
            200: NotificationSuccessResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
    mark_all_as_unseen=extend_schema(
        operation_id="markAllNotificationUsersAsUnseen",
        summary=_("Mark all notifications as unseen"),
        description=_(
            "Mark all of the authenticated user's notifications as unseen."
        ),
        tags=["Notification Users"],
        responses={
            200: NotificationSuccessResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
    mark_as_seen=extend_schema(
        operation_id="markNotificationUsersAsSeen",
        summary=_("Mark specific notifications as seen"),
        description=_(
            "Mark specific notifications as seen for the authenticated user."
        ),
        tags=["Notification Users"],
        request=NotificationUserActionSerializer,
        responses={
            200: NotificationSuccessResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
    mark_as_unseen=extend_schema(
        operation_id="markNotificationUsersAsUnseen",
        summary=_("Mark specific notifications as unseen"),
        description=_(
            "Mark specific notifications as unseen for the authenticated user."
        ),
        tags=["Notification Users"],
        request=NotificationUserActionSerializer,
        responses={
            200: NotificationSuccessResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["list", "retrieve"])
class NotificationUserViewSet(BaseModelViewSet):
    queryset = NotificationUser.objects.optimized_for_list()
    request_serializers = req_serializers
    response_serializers = res_serializers

    filterset_class = NotificationUserFilter
    ordering_fields = [
        "id",
        "user",
        "user__email",
        "user__first_name",
        "user__last_name",
        "notification",
        "notification__kind",
        "notification__category",
        "notification__priority",
        "notification__created_at",
        "seen",
        "seen_at",
        "created_at",
        "updated_at",
    ]
    ordering = ["-created_at", "-notification__created_at"]
    search_fields = [
        "user__first_name",
        "user__last_name",
        "user__email",
        "user__username",
        "notification__translations__title",
        "notification__translations__message",
        "notification__notification_type",
        "notification__link",
    ]

    @action(detail=False, methods=["GET"])
    def unseen_count(self, request):
        if request.user.is_anonymous:
            return Response(
                {
                    "error": _("User is not authenticated."),
                    "count": 0,
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        count = self.queryset.filter(user=request.user, seen=False).count()
        if count == 0:
            return Response(
                {
                    "count": 0,
                },
            )

        response_data = {"count": count}

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(response_data)
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["POST"])
    def mark_all_as_seen(self, request):
        if request.user.is_anonymous:
            return Response(
                {
                    "error": _("User is not authenticated."),
                    "success": False,
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        self.queryset.filter(user=request.user, seen=False).update(seen=True)

        response_data = {"success": True}

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(response_data)
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["POST"])
    def mark_all_as_unseen(self, request):
        if request.user.is_anonymous:
            return Response(
                {
                    "error": _("User is not authenticated."),
                    "success": False,
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        self.queryset.filter(user=request.user, seen=True).update(seen=False)

        response_data = {"success": True}

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(response_data)
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["POST"])
    def mark_as_seen(self, request):
        if request.user.is_anonymous:
            return Response(
                {
                    "error": _("User is not authenticated."),
                    "success": False,
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        request_serializer_class = self.get_request_serializer()
        serializer = request_serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        notification_user_ids = serializer.validated_data.get(
            "notification_user_ids"
        )

        if not notification_user_ids:
            return Response(
                {
                    "error": _("No notification user ids provided."),
                    "success": False,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        self.queryset.filter(
            id__in=notification_user_ids, user=request.user
        ).update(seen=True)

        response_data = {"success": True}

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(response_data)
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["POST"])
    def mark_as_unseen(self, request):
        if request.user.is_anonymous:
            return Response(
                {
                    "error": _("User is not authenticated."),
                    "success": False,
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        request_serializer_class = self.get_request_serializer()
        serializer = request_serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        notification_user_ids = serializer.validated_data.get(
            "notification_user_ids"
        )

        if not notification_user_ids:
            return Response(
                {
                    "error": _("No notification user ids provided."),
                    "success": False,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        self.queryset.filter(
            id__in=notification_user_ids, user=request.user
        ).update(seen=False)

        response_data = {"success": True}

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(response_data)
        return Response(response_serializer.data, status=status.HTTP_200_OK)
