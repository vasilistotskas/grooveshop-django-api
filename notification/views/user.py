from __future__ import annotations

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from core.api.views import BaseExpandView
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import MultiSerializerMixin
from notification.models.user import NotificationUser
from notification.paginators.user import NotificationUserPagination
from notification.serializers.user import NotificationUserActionSerializer
from notification.serializers.user import NotificationUserSerializer


class NotificationUserViewSet(MultiSerializerMixin, BaseExpandView, ModelViewSet):
    queryset = NotificationUser.objects.all()
    pagination_class = NotificationUserPagination
    filter_backends = [DjangoFilterBackend, PascalSnakeCaseOrderingFilter, SearchFilter]
    search_fields = ["user__id", "notification__id"]
    ordering_fields = ["id", "user", "notification", "seen"]

    serializers = {
        "default": NotificationUserSerializer,
        "list": NotificationUserSerializer,
        "mark_as_seen": NotificationUserActionSerializer,
        "mark_as_unseen": NotificationUserActionSerializer,
    }

    def list(self, request, *args, **kwargs) -> Response:
        if request.user.is_anonymous:
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        queryset = self.queryset.filter(user=request.user, seen=False)
        pagination_param = request.query_params.get("pagination", "true")

        if pagination_param.lower() == "false":
            serializer = self.get_serializer(queryset, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["GET"])
    def unseen_count(self, request):
        if request.user.is_anonymous:
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        count = self.queryset.filter(user=request.user, seen=False).count()
        return Response({"count": count}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["POST"])
    def mark_all_as_seen(self, request):
        if request.user.is_anonymous:
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        self.queryset.filter(user=request.user, seen=False).update(seen=True)
        return Response({"success": True}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["POST"])
    def mark_all_as_unseen(self, request):
        if request.user.is_anonymous:
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        self.queryset.filter(user=request.user, seen=True).update(seen=False)
        return Response({"success": True}, status=status.HTTP_200_OK)

    @action(
        detail=False,
        methods=["POST"],
    )
    def mark_as_seen(self, request):
        if request.user.is_anonymous:
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        notification_user_id = serializer.validated_data.get("notification_user_id")

        self.queryset.filter(id=notification_user_id, user=request.user).update(
            seen=True
        )
        return Response({"success": True}, status=status.HTTP_200_OK)

    @action(
        detail=False,
        methods=["POST"],
    )
    def mark_as_unseen(self, request):
        if request.user.is_anonymous:
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        notification_user_id = serializer.validated_data.get("notification_user_id")

        self.queryset.filter(id=notification_user_id, user=request.user).update(
            seen=False
        )
        return Response({"success": True}, status=status.HTTP_200_OK)
