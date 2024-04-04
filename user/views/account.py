from __future__ import annotations

from django.contrib.auth import get_user_model
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAdminUser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from authentication.serializers import AuthenticationSerializer
from core.api.permissions import IsStaffOrOwner
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from user.serializers.account import UserAccountDetailsSerializer

User = get_user_model()


class ObtainAuthTokenView(ObtainAuthToken):
    permission_classes = [IsAdminUser]


class UserAccountViewSet(BaseModelViewSet):
    permission_classes = [IsAuthenticated, IsStaffOrOwner]
    queryset = User.objects.all()
    serializer_class = AuthenticationSerializer
    filter_backends = [DjangoFilterBackend, PascalSnakeCaseOrderingFilter, SearchFilter]
    filterset_fields = ["id", "email"]
    ordering_fields = ["id", "email"]
    ordering = ["id"]
    search_fields = ["id", "email"]

    def get_queryset(self):
        if self.request.user.is_staff:
            return User.objects.all()
        else:
            return User.objects.filter(id=self.request.user.id)

    @action(detail=True, methods=["GET"])
    def details(self, request, pk=None, *args, **kwargs) -> Response:
        user_account = self.get_object()
        self.check_object_permissions(self.request, user_account)
        serializer = UserAccountDetailsSerializer(
            user_account, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=status.HTTP_200_OK)
