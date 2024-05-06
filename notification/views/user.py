from __future__ import annotations

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.renderers import BrowsableAPIRenderer
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response

from core.api.asynchronous.views import AsyncBaseAPIViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import MultiSerializerMixin
from notification.models.user import NotificationUser
from notification.serializers.user import NotificationUserActionSerializer
from notification.serializers.user import NotificationUserSerializer


class NotificationUserViewSet(MultiSerializerMixin, AsyncBaseAPIViewSet):
    queryset = NotificationUser.objects.all()
    filter_backends = [DjangoFilterBackend, PascalSnakeCaseOrderingFilter, SearchFilter]
    search_fields = ["user__id", "notification__id"]
    ordering_fields = ["id", "user", "notification", "seen", "created_at"]

    serializers = {
        "default": NotificationUserSerializer,
        "mark_as_seen": NotificationUserActionSerializer,
        "mark_as_unseen": NotificationUserActionSerializer,
    }

    @action(detail=False, methods=["GET"])
    async def unseen_count(self, request, *args, **kwargs):
        if request.user.is_anonymous:
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        queryset = self.get_queryset()
        count = await queryset.filter(user=request.user, seen=False).acount()
        return Response({"count": count}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["POST"])
    async def mark_all_as_seen(self, request, *args, **kwargs):
        if request.user.is_anonymous:
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        queryset = self.get_queryset()
        await queryset.filter(user=request.user, seen=False).aupdate(seen=True)
        return Response({"success": True}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["POST"])
    async def mark_all_as_unseen(self, request, *args, **kwargs):
        if request.user.is_anonymous:
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        queryset = self.get_queryset()
        await queryset.filter(user=request.user, seen=True).aupdate(seen=False)
        return Response({"success": True}, status=status.HTTP_200_OK)

    @action(
        detail=False,
        methods=["POST"],
    )
    async def mark_as_seen(self, request, *args, **kwargs):
        if request.user.is_anonymous:
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        queryset = self.get_queryset()
        serializer = NotificationUserSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        notification_user_id = serializer.validated_data.get("notification_user_id")

        await queryset.filter(id=notification_user_id, user=request.user).aupdate(
            seen=True
        )
        return Response({"success": True}, status=status.HTTP_200_OK)

    @action(
        detail=False,
        methods=["POST"],
    )
    async def mark_as_unseen(self, request, *args, **kwargs):
        if request.user.is_anonymous:
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        queryset = self.get_queryset()
        serializer = NotificationUserSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        notification_user_id = serializer.validated_data.get("notification_user_id")

        await queryset.filter(id=notification_user_id, user=request.user).aupdate(
            seen=False
        )
        return Response({"success": True}, status=status.HTTP_200_OK)
