from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.response import Response

from core.api.serializers import ErrorResponseSerializer
from core.api.views import PaginationModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import MultiSerializerMixin
from notification.models.user import NotificationUser
from notification.serializers.user import (
    NotificationCountResponseSerializer,
    NotificationInfoResponseSerializer,
    NotificationSuccessResponseSerializer,
    NotificationUserActionSerializer,
    NotificationUserSerializer,
)


@extend_schema_view(
    list=extend_schema(
        summary=_("List notification users"),
        description=_(
            "Retrieve a list of notification users with filtering and search capabilities."
        ),
        tags=["Notifications"],
        responses={
            200: NotificationUserSerializer(many=True),
        },
    ),
    create=extend_schema(
        summary=_("Create a notification user"),
        description=_(
            "Create a new notification user record. Requires authentication."
        ),
        tags=["Notifications"],
        responses={
            201: NotificationUserSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
    retrieve=extend_schema(
        summary=_("Retrieve a notification user"),
        description=_(
            "Get detailed information about a specific notification user."
        ),
        tags=["Notifications"],
        responses={
            200: NotificationUserSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    update=extend_schema(
        summary=_("Update a notification user"),
        description=_(
            "Update notification user information. Requires authentication."
        ),
        tags=["Notifications"],
        responses={
            200: NotificationUserSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    partial_update=extend_schema(
        summary=_("Partially update a notification user"),
        description=_(
            "Partially update notification user information. Requires authentication."
        ),
        tags=["Notifications"],
        responses={
            200: NotificationUserSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    destroy=extend_schema(
        summary=_("Delete a notification user"),
        description=_(
            "Delete a notification user record. Requires authentication."
        ),
        tags=["Notifications"],
        responses={
            204: None,
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    unseen_count=extend_schema(
        summary=_("Get unseen notifications count"),
        description=_(
            "Get the count of unseen notifications for the authenticated user."
        ),
        tags=["Notifications"],
        responses={
            200: NotificationCountResponseSerializer,
            204: NotificationInfoResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
    mark_all_as_seen=extend_schema(
        summary=_("Mark all notifications as seen"),
        description=_(
            "Mark all of the authenticated user's notifications as seen."
        ),
        tags=["Notifications"],
        responses={
            200: NotificationSuccessResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
    mark_all_as_unseen=extend_schema(
        summary=_("Mark all notifications as unseen"),
        description=_(
            "Mark all of the authenticated user's notifications as unseen."
        ),
        tags=["Notifications"],
        responses={
            200: NotificationSuccessResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
    mark_as_seen=extend_schema(
        summary=_("Mark specific notifications as seen"),
        description=_(
            "Mark specific notifications as seen for the authenticated user."
        ),
        tags=["Notifications"],
        request=NotificationUserActionSerializer,
        responses={
            200: NotificationSuccessResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
    mark_as_unseen=extend_schema(
        summary=_("Mark specific notifications as unseen"),
        description=_(
            "Mark specific notifications as unseen for the authenticated user."
        ),
        tags=["Notifications"],
        request=NotificationUserActionSerializer,
        responses={
            200: NotificationSuccessResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
        },
    ),
)
class NotificationUserViewSet(MultiSerializerMixin, PaginationModelViewSet):
    queryset = NotificationUser.objects.all()
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    search_fields = ["user__id", "notification__id"]
    ordering_fields = ["id", "user", "notification", "seen"]
    filterset_fields = [
        "seen",
        "notification__kind",
    ]

    serializers = {
        "default": NotificationUserSerializer,
        "mark_as_seen": NotificationUserActionSerializer,
        "mark_as_unseen": NotificationUserActionSerializer,
    }

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
