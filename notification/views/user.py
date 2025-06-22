from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.response import Response

from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import (
    MultiSerializerMixin,
    create_schema_view_config,
)
from core.utils.views import cache_methods
from notification.filters import NotificationUserFilter
from notification.models.user import NotificationUser
from notification.serializers.user import (
    NotificationCountResponseSerializer,
    NotificationInfoResponseSerializer,
    NotificationSuccessResponseSerializer,
    NotificationUserActionSerializer,
    NotificationUserDetailSerializer,
    NotificationUserSerializer,
    NotificationUserWriteSerializer,
)


@extend_schema_view(
    **create_schema_view_config(
        model_class=NotificationUser,
        display_config={
            "tag": "Notification Users",
        },
        serializers={
            "list_serializer": NotificationUserSerializer,
            "detail_serializer": NotificationUserDetailSerializer,
            "write_serializer": NotificationUserWriteSerializer,
        },
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
            204: NotificationInfoResponseSerializer,
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
class NotificationUserViewSet(MultiSerializerMixin, BaseModelViewSet):
    queryset = NotificationUser.objects.optimized_for_list()
    serializers = {
        "default": NotificationUserDetailSerializer,
        "list": NotificationUserSerializer,
        "retrieve": NotificationUserDetailSerializer,
        "create": NotificationUserWriteSerializer,
        "update": NotificationUserWriteSerializer,
        "partial_update": NotificationUserWriteSerializer,
        "mark_as_seen": NotificationUserActionSerializer,
        "mark_as_unseen": NotificationUserActionSerializer,
    }
    response_serializers = {
        "create": NotificationUserDetailSerializer,
        "update": NotificationUserDetailSerializer,
        "partial_update": NotificationUserDetailSerializer,
    }
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_class = NotificationUserFilter
    ordering_fields = [
        "id",
        "user",
        "notification",
        "seen",
        "seen_at",
        "created_at",
        "updated_at",
    ]
    ordering = ["-created_at"]
    search_fields = [
        "user__first_name",
        "user__last_name",
        "user__email",
        "notification__translations__title",
        "notification__translations__message",
    ]

    @action(detail=False, methods=["GET"])
    def unseen_count(self, request):
        if request.user.is_anonymous:
            return Response(
                {"error": _("User is not authenticated.")},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        count = self.queryset.filter(user=request.user, seen=False).count()
        if count == 0:
            return Response(
                {"info": _("No unseen notifications.")},
                status=status.HTTP_204_NO_CONTENT,
            )

        return Response({"count": count}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["POST"])
    def mark_all_as_seen(self, request):
        if request.user.is_anonymous:
            return Response(
                {"error": _("User is not authenticated.")},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        self.queryset.filter(user=request.user, seen=False).update(seen=True)
        return Response({"success": True}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["POST"])
    def mark_all_as_unseen(self, request):
        if request.user.is_anonymous:
            return Response(
                {"error": _("User is not authenticated.")},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        self.queryset.filter(user=request.user, seen=True).update(seen=False)
        return Response({"success": True}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["POST"])
    def mark_as_seen(self, request):
        if request.user.is_anonymous:
            return Response(
                {"error": _("User is not authenticated.")},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        notification_user_ids = serializer.validated_data.get(
            "notification_user_ids"
        )

        if not notification_user_ids:
            return Response(
                {"error": _("No notification user ids provided.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        self.queryset.filter(
            id__in=notification_user_ids, user=request.user
        ).update(seen=True)
        return Response({"success": True}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["POST"])
    def mark_as_unseen(self, request):
        if request.user.is_anonymous:
            return Response(
                {"error": _("User is not authenticated.")},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        notification_user_ids = serializer.validated_data.get(
            "notification_user_ids"
        )

        if not notification_user_ids:
            return Response(
                {"error": _("No notification user ids provided.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        self.queryset.filter(
            id__in=notification_user_ids, user=request.user
        ).update(seen=False)
        return Response({"success": True}, status=status.HTTP_200_OK)
