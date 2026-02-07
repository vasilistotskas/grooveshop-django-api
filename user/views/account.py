from __future__ import annotations

from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_view
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
    ActionConfig,
    SerializersConfig,
    create_schema_view_config,
    crud_config,
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

serializers_config: SerializersConfig = {
    **crud_config(
        list=UserDetailsSerializer,
        detail=UserDetailsSerializer,
        write=UserWriteSerializer,
    ),
    "favourite_products": ActionConfig(
        response=ProductFavouriteSerializer,
        many=True,
        operation_id="getUserAccountFavouriteProducts",
        summary=_("Get user's favourite products"),
        description=_("Get all favourite products for a specific user."),
        tags=["User Accounts"],
    ),
    "orders": ActionConfig(
        response=OrderSerializer,
        many=True,
        operation_id="getUserAccountOrders",
        summary=_("Get user's orders"),
        description=_("Get all orders for a specific user."),
        tags=["User Accounts"],
    ),
    "product_reviews": ActionConfig(
        response=ProductReviewSerializer,
        many=True,
        operation_id="getUserAccountProductReviews",
        summary=_("Get user's product reviews"),
        description=_("Get all product reviews written by a specific user."),
        tags=["User Accounts"],
    ),
    "addresses": ActionConfig(
        response=UserAddressSerializer,
        many=True,
        operation_id="getUserAccountAddresses",
        summary=_("Get user's addresses"),
        description=_("Get all addresses for a specific user."),
        tags=["User Accounts"],
    ),
    "blog_post_comments": ActionConfig(
        response=BlogCommentSerializer,
        many=True,
        operation_id="getUserAccountBlogPostComments",
        summary=_("Get user's blog comments"),
        description=_("Get all blog post comments written by a specific user."),
        tags=["User Accounts"],
    ),
    "liked_blog_posts": ActionConfig(
        response=BlogPostSerializer,
        many=True,
        operation_id="getUserAccountLikedBlogPosts",
        summary=_("Get user's liked blog posts"),
        description=_("Get all blog posts liked by a specific user."),
        tags=["User Accounts"],
    ),
    "notifications": ActionConfig(
        response=NotificationUserSerializer,
        many=True,
        operation_id="getUserAccountNotifications",
        summary=_("Get user's notifications"),
        description=_("Get all notifications for a specific user."),
        tags=["User Accounts"],
    ),
    "subscriptions": ActionConfig(
        response=UserSubscriptionSerializer,
        many=True,
        operation_id="getUserAccountSubscriptions",
        summary=_("Get user's subscriptions"),
        description=_("Get all subscriptions for a specific user."),
        tags=["User Accounts"],
    ),
    "change_username": ActionConfig(
        request=UsernameUpdateSerializer,
        response=UsernameUpdateResponseSerializer,
        operation_id="changeUserAccountUsername",
        summary=_("Change username"),
        description=_("Change the username for a specific user."),
        tags=["User Accounts"],
    ),
    "subscription_summary": ActionConfig(
        response=UserSubscriptionSummaryResponseSerializer,
        operation_id="getUserAccountSubscriptionSummary",
        summary=_("Get user's subscription summary"),
        description=_("Get a summary of subscriptions for a specific user."),
        tags=["User Accounts"],
    ),
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=User,
        display_config={
            "tag": "User Accounts",
        },
        serializers_config=serializers_config,
        error_serializer=ErrorResponseSerializer,
    ),
)
class UserAccountViewSet(BaseModelViewSet):
    queryset = User.objects.none()
    serializers_config = serializers_config
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
                from product.models.favourite import ProductFavourite

                user = get_object_or_404(User, id=self.kwargs["pk"])
                queryset = ProductFavourite.objects.for_list().filter(user=user)
            case "orders":
                from order.models.order import Order

                user = get_object_or_404(User, id=self.kwargs["pk"])
                queryset = Order.objects.for_list().filter(user=user)
            case "product_reviews":
                from product.models.review import ProductReview

                user = get_object_or_404(User, id=self.kwargs["pk"])
                queryset = ProductReview.objects.for_list().filter(user=user)
            case "addresses":
                from user.models.address import UserAddress

                user = get_object_or_404(User, id=self.kwargs["pk"])
                queryset = UserAddress.objects.for_list().filter(user=user)
            case "blog_post_comments":
                from blog.models.comment import BlogComment

                user = get_object_or_404(User, id=self.kwargs["pk"])
                queryset = BlogComment.objects.for_list().filter(user=user)
            case "liked_blog_posts":
                from blog.models.post import BlogPost

                user = get_object_or_404(User, id=self.kwargs["pk"])
                queryset = BlogPost.objects.for_list().filter(likes=user)
            case "notifications":
                from notification.models import NotificationUser

                user = get_object_or_404(User, id=self.kwargs["pk"])
                queryset = NotificationUser.objects.for_list().filter(user=user)
            case "subscriptions":
                user = get_object_or_404(User, id=self.kwargs["pk"])
                queryset = user.subscriptions.select_related("topic")
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
