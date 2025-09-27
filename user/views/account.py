from __future__ import annotations

from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
)
from rest_framework import status
from rest_framework.decorators import action

from rest_framework.generics import get_object_or_404
from rest_framework.response import Response

from user.serializers.account import (
    UserDetailsSerializer,
    UserWriteSerializer,
)
from blog.filters.comment import BlogCommentFilter
from blog.filters.post import BlogPostFilter
from blog.serializers.comment import BlogCommentSerializer
from blog.serializers.post import BlogPostSerializer
from core.api.permissions import IsOwnerOrAdmin
from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet

from core.utils.serializers import (
    create_schema_view_config,
    RequestSerializersConfig,
    ResponseSerializersConfig,
)
from notification.filters import NotificationUserFilter
from notification.serializers.user import NotificationUserSerializer
from order.filters import OrderFilter
from order.serializers.order import OrderSerializer
from product.filters.favourite import ProductFavouriteFilter
from product.filters.review import ProductReviewFilter
from product.serializers.favourite import ProductFavouriteSerializer
from product.serializers.review import ProductReviewSerializer
from user.filters import UserAddressFilter, UserSubscriptionFilter
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

req_serializers: RequestSerializersConfig = {
    "create": UserWriteSerializer,
    "update": UserWriteSerializer,
    "partial_update": UserWriteSerializer,
    "change_username": UsernameUpdateSerializer,
    "subscriptions": None,
}

res_serializers: ResponseSerializersConfig = {
    "create": UserDetailsSerializer,
    "list": UserDetailsSerializer,
    "retrieve": UserDetailsSerializer,
    "update": UserDetailsSerializer,
    "partial_update": UserDetailsSerializer,
    "favourite_products": ProductFavouriteSerializer,
    "orders": OrderSerializer,
    "product_reviews": ProductReviewSerializer,
    "addresses": UserAddressSerializer,
    "blog_post_comments": BlogCommentSerializer,
    "liked_blog_posts": BlogPostSerializer,
    "notifications": NotificationUserSerializer,
    "subscriptions": UserSubscriptionSerializer,
    "change_username": UsernameUpdateResponseSerializer,
    "subscription_summary": UserSubscriptionSummaryResponseSerializer,
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=User,
        display_config={
            "tag": "User Accounts",
        },
        request_serializers=req_serializers,
        response_serializers=res_serializers,
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
class UserAccountViewSet(BaseModelViewSet):
    queryset = User.objects.none()
    request_serializers = req_serializers
    response_serializers = res_serializers
    permission_classes = [IsOwnerOrAdmin]
    ordering_fields = ["id", "email", "username", "created_at", "updated_at"]
    ordering = ["-created_at"]
    search_fields = ["id", "email", "username", "first_name", "last_name"]

    def get_filterset_class(self):
        action_filter_map = {
            "favourite_products": ProductFavouriteFilter,
            "orders": OrderFilter,
            "product_reviews": ProductReviewFilter,
            "addresses": UserAddressFilter,
            "blog_post_comments": BlogCommentFilter,
            "liked_blog_posts": BlogPostFilter,
            "notifications": NotificationUserFilter,
            "subscriptions": UserSubscriptionFilter,
        }

        if self.action in action_filter_map:
            return action_filter_map[self.action]
        return UserAccountFilter

    def get_queryset(self):
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
        self.ordering_fields = []
        self.ordering = []
        self.search_fields = []

        queryset = self.filter_queryset(self.get_queryset())

        response_serializer_class = self.get_response_serializer()
        return self.paginate_and_serialize(
            queryset, request, serializer_class=response_serializer_class
        )

    @action(detail=True, methods=["GET"])
    def orders(self, request, pk=None):
        self.ordering_fields = []
        self.ordering = []
        self.search_fields = []

        queryset = self.filter_queryset(self.get_queryset())

        response_serializer_class = self.get_response_serializer()
        return self.paginate_and_serialize(
            queryset, request, serializer_class=response_serializer_class
        )

    @action(detail=True, methods=["GET"])
    def product_reviews(self, request, pk=None):
        self.ordering_fields = []
        self.ordering = []
        self.search_fields = []

        queryset = self.filter_queryset(self.get_queryset())

        response_serializer_class = self.get_response_serializer()
        return self.paginate_and_serialize(
            queryset, request, serializer_class=response_serializer_class
        )

    @action(detail=True, methods=["GET"])
    def addresses(self, request, pk=None):
        self.ordering_fields = []
        self.ordering = []
        self.search_fields = []

        queryset = self.filter_queryset(self.get_queryset())

        response_serializer_class = self.get_response_serializer()
        return self.paginate_and_serialize(
            queryset, request, serializer_class=response_serializer_class
        )

    @action(detail=True, methods=["GET"])
    def blog_post_comments(self, request, pk=None):
        self.ordering_fields = []
        self.ordering = []
        self.search_fields = []

        queryset = self.filter_queryset(self.get_queryset())

        response_serializer_class = self.get_response_serializer()
        return self.paginate_and_serialize(
            queryset, request, serializer_class=response_serializer_class
        )

    @action(detail=True, methods=["GET"])
    def liked_blog_posts(self, request, pk=None):
        self.ordering_fields = []
        self.ordering = []
        self.search_fields = []

        queryset = self.filter_queryset(self.get_queryset())

        response_serializer_class = self.get_response_serializer()
        return self.paginate_and_serialize(
            queryset, request, serializer_class=response_serializer_class
        )

    @action(detail=True, methods=["GET"])
    def notifications(self, request, pk=None):
        self.ordering_fields = []
        self.ordering = []
        self.search_fields = []

        queryset = self.filter_queryset(self.get_queryset())

        response_serializer_class = self.get_response_serializer()
        return self.paginate_and_serialize(
            queryset, request, serializer_class=response_serializer_class
        )

    @action(detail=True, methods=["GET", "POST"])
    def subscriptions(self, request, pk=None):
        user = self.get_object()

        if request.method == "GET":
            self.ordering_fields = []
            self.ordering = []
            self.search_fields = []

            queryset = self.filter_queryset(self.get_queryset())

            response_serializer_class = self.get_response_serializer()
            return self.paginate_and_serialize(
                queryset, request, serializer_class=response_serializer_class
            )

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

            response_serializer_class = self.get_response_serializer()
            response_serializer = response_serializer_class(subscription)
            return Response(
                response_serializer.data, status=status.HTTP_201_CREATED
            )

    @action(detail=True, methods=["POST"])
    def change_username(self, request, pk=None):
        user = self.get_object()

        request_serializer_class = self.get_request_serializer()
        request_serializer = request_serializer_class(data=request.data)
        if request_serializer.is_valid():
            new_username = request_serializer.validated_data["username"]

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

            response_data = {"detail": _("Username updated successfully.")}

            response_serializer_class = self.get_response_serializer()
            response_serializer = response_serializer_class(response_data)
            return Response(response_serializer.data, status=status.HTTP_200_OK)

        return Response(
            request_serializer.errors, status=status.HTTP_400_BAD_REQUEST
        )

    @action(detail=True, methods=["GET"])
    def subscription_summary(self, request, pk=None):
        self.ordering_fields = []
        self.ordering = []
        self.search_fields = []

        user = self.get_object()
        summary = get_user_subscription_summary(user)

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(summary)
        return Response(response_serializer.data)
