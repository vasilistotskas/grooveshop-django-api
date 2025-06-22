from __future__ import annotations

from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
)
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from authentication.serializers import AuthenticationSerializer
from blog.serializers.comment import BlogCommentSerializer
from blog.serializers.post import BlogPostSerializer
from core.api.permissions import IsSelfOrAdmin
from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import (
    MultiSerializerMixin,
    create_schema_view_config,
)
from notification.serializers.user import NotificationUserSerializer
from order.serializers.order import OrderSerializer
from product.serializers.favourite import ProductFavouriteSerializer
from product.serializers.review import ProductReviewSerializer
from user.filters.account import UserAccountFilter
from user.models.subscription import SubscriptionTopic, UserSubscription
from user.serializers.account import (
    UsernameUpdateResponseSerializer,
    UsernameUpdateSerializer,
    UserSubscriptionSummaryResponseSerializer,
)
from user.serializers.address import UserAddressSerializer
from user.serializers.subscription import UserSubscriptionSerializer
from user.utils.subscription import get_user_subscription_summary

User = get_user_model()


@extend_schema_view(
    **create_schema_view_config(
        model_class=User,
        display_config={
            "tag": "User Accounts",
        },
        serializers={
            "list_serializer": AuthenticationSerializer,
            "detail_serializer": AuthenticationSerializer,
            "write_serializer": AuthenticationSerializer,
        },
        error_serializer=ErrorResponseSerializer,
    ),
    favourite_products=extend_schema(
        operation_id="getUserAccountFavouriteProducts",
        summary=_("Get user's favourite products"),
        description=_("Get all favourite products for a specific user."),
        tags=["User Accounts"],
        responses={
            200: ProductFavouriteSerializer(many=True),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    orders=extend_schema(
        operation_id="getUserAccountOrders",
        summary=_("Get user's orders"),
        description=_("Get all orders for a specific user."),
        tags=["User Accounts"],
        responses={
            200: OrderSerializer(many=True),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    product_reviews=extend_schema(
        operation_id="getUserAccountProductReviews",
        summary=_("Get user's product reviews"),
        description=_("Get all product reviews written by a specific user."),
        tags=["User Accounts"],
        responses={
            200: ProductReviewSerializer(many=True),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    addresses=extend_schema(
        operation_id="getUserAccountAddresses",
        summary=_("Get user's addresses"),
        description=_("Get all addresses for a specific user."),
        tags=["User Accounts"],
        responses={
            200: UserAddressSerializer(many=True),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    blog_post_comments=extend_schema(
        operation_id="getUserAccountBlogPostComments",
        summary=_("Get user's blog comments"),
        description=_("Get all blog post comments written by a specific user."),
        tags=["User Accounts"],
        responses={
            200: BlogCommentSerializer(many=True),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    liked_blog_posts=extend_schema(
        operation_id="getUserAccountLikedBlogPosts",
        summary=_("Get user's liked blog posts"),
        description=_("Get all blog posts liked by a specific user."),
        tags=["User Accounts"],
        responses={
            200: BlogPostSerializer(many=True),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    notifications=extend_schema(
        operation_id="getUserAccountNotifications",
        summary=_("Get user's notifications"),
        description=_("Get all notifications for a specific user."),
        tags=["User Accounts"],
        responses={
            200: NotificationUserSerializer(many=True),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    subscriptions=extend_schema(
        operation_id="getUserAccountSubscriptions",
        summary=_("Get user's subscriptions"),
        description=_("Get all subscriptions for a specific user."),
        tags=["User Accounts"],
        responses={
            200: UserSubscriptionSerializer(many=True),
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    change_username=extend_schema(
        operation_id="changeUserAccountUsername",
        summary=_("Change username"),
        description=_("Change the username for a specific user."),
        tags=["User Accounts"],
        request=UsernameUpdateSerializer,
        responses={
            200: UsernameUpdateResponseSerializer,
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
    subscription_summary=extend_schema(
        operation_id="getUserAccountSubscriptionSummary",
        summary=_("Get user's subscription summary"),
        description=_("Get a summary of subscriptions for a specific user."),
        tags=["User Accounts"],
        responses={
            200: UserSubscriptionSummaryResponseSerializer,
            401: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    ),
)
class UserAccountViewSet(MultiSerializerMixin, BaseModelViewSet):
    permission_classes = [IsAuthenticated, IsSelfOrAdmin]
    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_class = UserAccountFilter
    ordering_fields = ["id", "email", "username", "created_at", "updated_at"]
    ordering = ["-created_at"]
    search_fields = ["id", "email", "username", "first_name", "last_name"]

    serializers = {
        "default": AuthenticationSerializer,
        "create": AuthenticationSerializer,
        "favourite_products": ProductFavouriteSerializer,
        "orders": OrderSerializer,
        "product_reviews": ProductReviewSerializer,
        "addresses": UserAddressSerializer,
        "blog_post_comments": BlogCommentSerializer,
        "liked_blog_posts": BlogPostSerializer,
        "notifications": NotificationUserSerializer,
        "subscriptions": UserSubscriptionSerializer,
        "change_username": UsernameUpdateSerializer,
    }

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return User.objects.none()

        match self.action:
            case "favourite_products":
                queryset = get_object_or_404(
                    User, id=self.kwargs["pk"]
                ).favourite_products.all()
            case "orders":
                queryset = get_object_or_404(
                    User, id=self.kwargs["pk"]
                ).orders.all()
            case "product_reviews":
                queryset = get_object_or_404(
                    User, id=self.kwargs["pk"]
                ).product_reviews.all()
            case "addresses":
                queryset = get_object_or_404(
                    User, id=self.kwargs["pk"]
                ).addresses.all()
            case "blog_post_comments":
                queryset = get_object_or_404(
                    User, id=self.kwargs["pk"]
                ).blog_comments.all()
            case "liked_blog_posts":
                queryset = get_object_or_404(
                    User, id=self.kwargs["pk"]
                ).liked_blog_posts.all()
            case "notifications":
                queryset = get_object_or_404(
                    User, id=self.kwargs["pk"]
                ).notification.all()
            case "subscriptions":
                queryset = get_object_or_404(
                    User, id=self.kwargs["pk"]
                ).subscriptions.select_related("topic")
            case _:
                queryset = (
                    User.objects.all()
                    if self.request.user.is_staff
                    else User.objects.filter(id=self.request.user.id)
                )

        return queryset

    def get_object(self):
        obj = super().get_object()
        self.check_object_permissions(self.request, obj)
        return obj

    @action(detail=True, methods=["GET"])
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

    @action(detail=True, methods=["GET"])
    def orders(self, request, pk=None):
        self.ordering_fields = ["created_at", "updated_at", "status"]
        self.filterset_fields = ["status"]
        self.ordering = ["-created_at"]
        self.search_fields = []
        queryset = self.filter_queryset(self.get_queryset())
        return self.paginate_and_serialize(queryset, request)

    @action(detail=True, methods=["GET"])
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

    @action(detail=True, methods=["GET"])
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

    @action(detail=True, methods=["GET"])
    def blog_post_comments(self, request, pk=None):
        self.filterset_fields = ["id", "post", "parent", "is_approved"]
        self.ordering_fields = ["id", "post", "created_at"]
        self.ordering = ["-created_at"]
        self.search_fields = ["id", "post"]
        queryset = self.filter_queryset(self.get_queryset())
        return self.paginate_and_serialize(queryset, request)

    @action(detail=True, methods=["GET"])
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

    @action(detail=True, methods=["GET"])
    def notifications(self, request, pk=None):
        self.filterset_fields = ["seen", "notification__kind"]
        self.ordering_fields = ["created_at", "seen_at"]
        self.ordering = ["-created_at"]

        queryset = self.filter_queryset(self.get_queryset())
        return self.paginate_and_serialize(queryset, request)

    @action(detail=True, methods=["GET", "POST"])
    def subscriptions(self, request, pk=None):
        user = self.get_object()

        if request.method == "GET":
            self.filterset_fields = ["topic", "status", "topic__category"]
            self.ordering_fields = ["subscribed_at", "created_at"]
            self.ordering = ["-subscribed_at"]
            self.search_fields = ["topic__name", "topic__description"]

            queryset = self.filter_queryset(self.get_queryset())
            return self.paginate_and_serialize(queryset, request)

        elif request.method == "POST":
            topic_id = request.data.get("topic_id")
            if not topic_id:
                return Response(
                    {"detail": _("Topic ID is required.")},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            try:
                topic = SubscriptionTopic.objects.get(
                    id=topic_id, is_active=True
                )
            except SubscriptionTopic.DoesNotExist:
                return Response(
                    {"detail": _("Topic not found or not active.")},
                    status=status.HTTP_404_NOT_FOUND,
                )

            subscription, created = UserSubscription.objects.get_or_create(
                user=user,
                topic=topic,
                defaults={
                    "status": UserSubscription.SubscriptionStatus.PENDING
                    if topic.requires_confirmation
                    else UserSubscription.SubscriptionStatus.ACTIVE
                },
            )

            if (
                not created
                and subscription.status
                == UserSubscription.SubscriptionStatus.ACTIVE
            ):
                return Response(
                    {"detail": _("User is already subscribed to this topic.")},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            serializer = UserSubscriptionSerializer(subscription)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["POST"])
    def change_username(self, request, pk=None):
        user = self.get_object()

        serializer = UsernameUpdateSerializer(data=request.data)
        if serializer.is_valid():
            new_username = serializer.validated_data["username"]

            if (
                User.objects.filter(username=new_username)
                .exclude(id=user.id)
                .exists()
            ):
                return Response(
                    {"detail": _("Username already exists.")},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            user.username = new_username
            user.save()

            return Response(
                {"detail": _("Username updated successfully.")},
                status=status.HTTP_200_OK,
            )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["GET"])
    def subscription_summary(self, request, pk=None):
        user = self.get_object()
        summary = get_user_subscription_summary(user)
        return Response(summary)
