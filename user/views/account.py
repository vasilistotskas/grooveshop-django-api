from __future__ import annotations

from django.contrib.auth import get_user_model
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAdminUser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from authentication.serializers import AuthenticationSerializer
from core.api.permissions import IsStaffOrOwner
from core.api.views import BaseExpandView
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from user.paginators.account import UserAccountPagination
from user.serializers.account import UserAccountDetailsSerializer

User = get_user_model()


class ObtainAuthTokenView(ObtainAuthToken):
    permission_classes = [IsAdminUser]


class UserAccountViewSet(BaseExpandView, ModelViewSet):
    permission_classes = [IsAuthenticated, IsStaffOrOwner]
    queryset = User.objects.all()
    serializer_class = AuthenticationSerializer
    pagination_class = UserAccountPagination
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

    def list(self, request, *args, **kwargs) -> Response:
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def create(self, request, *args, **kwargs) -> Response:
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def update(self, request, pk=None, *args, **kwargs) -> Response:
        user = get_object_or_404(User, id=pk)
        serializer = self.get_serializer(user, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def partial_update(self, request, pk=None, *args, **kwargs) -> Response:
        user = get_object_or_404(User, id=pk)
        serializer = self.get_serializer(user, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, pk=None, *args, **kwargs) -> Response:
        user = get_object_or_404(User, id=pk)
        user.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["GET"])
    def details(self, request, pk=None, *args, **kwargs) -> Response:
        user_account = get_object_or_404(User, id=pk)
        self.check_object_permissions(self.request, user_account)
        expand = self.request.query_params.get("expand", "false").lower()
        context = {"expand": expand}
        serializer = UserAccountDetailsSerializer(user_account, context=context)
        return Response(serializer.data, status=status.HTTP_200_OK)
