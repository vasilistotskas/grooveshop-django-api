from __future__ import annotations

from typing import override

from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from authentication.serializers import AuthenticationSerializer
from authentication.serializers import UsernameUpdateSerializer
from blog.serializers.comment import BlogCommentSerializer
from blog.serializers.post import BlogPostSerializer
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import MultiSerializerMixin
from notification.serializers.user import NotificationUserSerializer
from order.serializers.order import OrderSerializer
from product.serializers.favourite import ProductFavouriteSerializer
from product.serializers.review import ProductReviewSerializer
from user.serializers.address import UserAddressSerializer

User = get_user_model()


class UserAccountViewSet(MultiSerializerMixin, BaseModelViewSet):
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_fields = ["id", "email"]
    ordering_fields = ["id", "email"]
    ordering = ["-created_at"]
    search_fields = ["id", "email"]

    serializers = {
        "default": AuthenticationSerializer,
        "favourite_products": ProductFavouriteSerializer,
        "orders": OrderSerializer,
        "product_reviews": ProductReviewSerializer,
        "addresses": UserAddressSerializer,
        "blog_post_comments": BlogCommentSerializer,
        "liked_blog_posts": BlogPostSerializer,
        "notifications": NotificationUserSerializer,
        "change_username": UsernameUpdateSerializer,
    }

    @override
    def get_queryset(self):
        match self.action:
            case "favourite_products":
                queryset = get_object_or_404(User, id=self.kwargs["pk"]).favourite_products.all()
            case "orders":
                queryset = get_object_or_404(User, id=self.kwargs["pk"]).orders.all()
            case "product_reviews":
                queryset = get_object_or_404(User, id=self.kwargs["pk"]).product_reviews.all()
            case "addresses":
                queryset = get_object_or_404(User, id=self.kwargs["pk"]).addresses.all()
            case "blog_post_comments":
                queryset = get_object_or_404(User, id=self.kwargs["pk"]).blog_comments.all()
            case "liked_blog_posts":
                queryset = get_object_or_404(User, id=self.kwargs["pk"]).liked_blog_posts.all()
            case "notifications":
                queryset = get_object_or_404(User, id=self.kwargs["pk"]).notification.all()
            case _:
                queryset = (
                    User.objects.all() if self.request.user.is_staff else User.objects.filter(id=self.request.user.id)
                )

        return queryset

    @action(detail=True, methods=["GET"], permission_classes=[IsAuthenticated])
    def favourite_products(self, request, pk=None):
        self.filterset_fields = ["id"]
        self.ordering_fields = [
            "id",
            "product_id",
            "created_at",
            "updated_at",
        ]
        self.ordering = ["-updated_at"]
        self.search_fields = [
            "id",
            "product_id",
        ]
        queryset = self.filter_queryset(self.get_queryset())
        return self.paginate_and_serialize(queryset, request)

    @action(detail=True, methods=["GET"], permission_classes=[IsAuthenticated])
    def orders(self, request, pk=None):
        self.ordering_fields = ["created_at", "updated_at", "status"]
        self.filterset_fields = ["status"]
        self.ordering = ["-created_at"]
        self.search_fields = []
        queryset = self.filter_queryset(self.get_queryset())
        return self.paginate_and_serialize(queryset, request)

    @action(detail=True, methods=["GET"], permission_classes=[IsAuthenticated])
    def product_reviews(self, request, pk=None):
        self.filterset_fields = ["id", "product_id", "status"]
        self.ordering_fields = [
            "id",
            "product_id",
            "created_at",
            "updated_at",
        ]
        self.ordering = ["-created_at"]
        self.search_fields = [
            "id",
            "product_id",
        ]
        queryset = self.filter_queryset(self.get_queryset())
        return self.paginate_and_serialize(queryset, request)

    @action(detail=True, methods=["GET"], permission_classes=[IsAuthenticated])
    def addresses(self, request, pk=None):
        self.filterset_fields = [
            "id",
            "country",
            "city",
            "street",
            "zipcode",
            "floor",
            "location_type",
            "is_main",
        ]
        self.ordering_fields = [
            "id",
            "country",
            "zipcode",
            "floor",
            "location_type",
            "is_main",
            "created_at",
            "updated_at",
        ]
        self.ordering = ["-is_main", "-created_at"]
        self.search_fields = ["id", "country", "city", "street", "zipcode"]
        queryset = self.filter_queryset(self.get_queryset())
        return self.paginate_and_serialize(queryset, request)

    @action(detail=True, methods=["GET"], permission_classes=[IsAuthenticated])
    def blog_post_comments(self, request, pk=None):
        self.filterset_fields = ["id", "post", "parent", "is_approved"]
        self.ordering_fields = ["id", "post", "created_at"]
        self.ordering = ["-created_at"]
        self.search_fields = ["id", "post"]
        queryset = self.filter_queryset(self.get_queryset())
        return self.paginate_and_serialize(queryset, request)

    @action(detail=True, methods=["GET"], permission_classes=[IsAuthenticated])
    def liked_blog_posts(self, request, pk=None):
        self.filterset_fields = ["id", "tags", "slug", "author"]
        self.ordering_fields = [
            "id",
            "title",
            "created_at",
            "updated_at",
            "published_at",
        ]
        self.ordering = ["-created_at"]
        self.search_fields = ["id", "title", "subtitle", "body"]
        queryset = self.filter_queryset(self.get_queryset())
        return self.paginate_and_serialize(queryset, request)

    @action(detail=True, methods=["GET"], permission_classes=[IsAuthenticated])
    def notifications(self, request, pk=None):
        self.filterset_fields = ["seen", "notification__kind"]
        self.ordering_fields = ["created_at", "seen_at"]
        self.ordering = ["-created_at"]

        queryset = self.filter_queryset(self.get_queryset())
        return self.paginate_and_serialize(queryset, request)

    @action(detail=True, methods=["POST"], permission_classes=[IsAuthenticated])
    def change_username(self, request, pk=None):
        user = self.get_object()
        serializer = UsernameUpdateSerializer(data=request.data)

        if serializer.is_valid():
            new_username = serializer.validated_data.get("username")

            if user.username == new_username:
                return Response(
                    {"detail": _("The new username is the same as the current username.")},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if User.objects.filter(username=new_username).exists():
                return Response(
                    {"detail": _("Username already taken.")},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            user.username = new_username
            user.save()
            return Response(
                {"detail": _("Username updated successfully.")},
                status=status.HTTP_200_OK,
            )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
