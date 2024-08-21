from __future__ import annotations

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.api.views import ExpandModelViewSet
from core.api.views import PaginationModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import MultiSerializerMixin
from notification.models.user import NotificationUser
from notification.serializers.user import NotificationUserActionSerializer
from notification.serializers.user import NotificationUserSerializer


class NotificationUserViewSet(MultiSerializerMixin, ExpandModelViewSet, PaginationModelViewSet):
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

    @action(detail=False, methods=["GET"], permission_classes=[IsAuthenticated])
    def unseen_count(self, request):
        if request.user.is_anonymous:
            return Response({"count": 0}, status=status.HTTP_401_UNAUTHORIZED)

        count = self.queryset.filter(user=request.user, seen=False).count()
        if count == 0:
            return Response({"count": count}, status=status.HTTP_204_NO_CONTENT)

        return Response({"count": count}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["POST"], permission_classes=[IsAuthenticated])
    def mark_all_as_seen(self, request):
        if request.user.is_anonymous:
            return Response({"success": False}, status=status.HTTP_401_UNAUTHORIZED)

        self.queryset.filter(user=request.user, seen=False).update(seen=True)
        return Response({"success": True}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["POST"], permission_classes=[IsAuthenticated])
    def mark_all_as_unseen(self, request):
        if request.user.is_anonymous:
            return Response({"success": False}, status=status.HTTP_401_UNAUTHORIZED)

        self.queryset.filter(user=request.user, seen=True).update(seen=False)
        return Response({"success": True}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["POST"], permission_classes=[IsAuthenticated])
    def mark_as_seen(self, request):
        if request.user.is_anonymous:
            return Response({"success": False}, status=status.HTTP_401_UNAUTHORIZED)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        notification_user_ids = serializer.validated_data.get("notification_user_ids")

        if not notification_user_ids:
            return Response({"success": False}, status=status.HTTP_400_BAD_REQUEST)

        self.queryset.filter(id__in=notification_user_ids, user=request.user).update(seen=True)
        return Response({"success": True}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["POST"], permission_classes=[IsAuthenticated])
    def mark_as_unseen(self, request):
        if request.user.is_anonymous:
            return Response({"success": False}, status=status.HTTP_401_UNAUTHORIZED)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        notification_user_ids = serializer.validated_data.get("notification_user_ids")

        if not notification_user_ids:
            return Response({"success": False}, status=status.HTTP_400_BAD_REQUEST)

        self.queryset.filter(id__in=notification_user_ids, user=request.user).update(seen=False)
        return Response({"success": True}, status=status.HTTP_200_OK)
