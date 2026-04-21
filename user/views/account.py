from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.utils.translation import gettext_lazy as _
from drf_spectacular.openapi import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema_view
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
from notification.serializers.user import NotificationUserDetailSerializer
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
    DeleteAccountRequestSerializer,
    DeleteAccountResponseSerializer,
    UsernameUpdateResponseSerializer,
    UsernameUpdateSerializer,
    UserDataExportSerializer,
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
        response=NotificationUserDetailSerializer,
        many=True,
        operation_id="getUserAccountNotifications",
        summary=_("Get user's notifications"),
        description=_("Get all notifications for a specific user."),
        tags=["User Accounts"],
        # Advertise the seen filter (comes from NotificationUserFilter on
        # the ViewSet) so the generated OpenAPI schema matches reality.
        # Without this, ``drf-spectacular`` only picks up the ordering /
        # pagination / search params and the Nuxt client has no way to
        # pass ``?seen=true|false`` via the generated types.
        parameters=[
            OpenApiParameter(
                name="seen",
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                required=False,
                description=_("Filter notifications by seen/unseen state."),
            ),
        ],
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
    "request_data_export": ActionConfig(
        response=UserDataExportSerializer,
        operation_id="requestUserAccountDataExport",
        summary=_("Request GDPR data export"),
        description=_(
            "Queue a job that compiles the user's personal data and emails "
            "a one-off download link. Returns the new UserDataExport record."
        ),
        tags=["User Accounts"],
    ),
    "data_exports": ActionConfig(
        response=UserDataExportSerializer,
        many=True,
        operation_id="listUserAccountDataExports",
        summary=_("List GDPR data exports"),
        description=_(
            "List recent data-export requests for this user so the UI can "
            "show progress and the last download link."
        ),
        tags=["User Accounts"],
    ),
    "delete_account": ActionConfig(
        request=DeleteAccountRequestSerializer,
        response=DeleteAccountResponseSerializer,
        operation_id="deleteUserAccountGdpr",
        summary=_("Delete account (GDPR right-to-erasure)"),
        description=_(
            "Anonymises the user's orders (kept for tax/accounting) and "
            "hard-deletes every other linked record. Irreversible. "
            "Caller must have re-authenticated via allauth within the "
            "window configured by ``ACCOUNT_REAUTHENTICATION_TIMEOUT``."
        ),
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

    # Per-action filtersets — each nested action operates over a
    # different model (orders, favourites, notifications, …) so we
    # switch filter classes based on ``self.action``.
    #
    # NOTE: ``django-filter``'s ``DjangoFilterBackend`` reads the
    # ``filterset_class`` *attribute* via ``getattr(view,
    # 'filterset_class', None)`` — it does NOT call this method. Just
    # defining ``get_filterset_class`` as a method silently bound no
    # filter at all on every nested action (unseen/seen tab on the
    # notifications page returned identical counts because ``seen``
    # was never forwarded to the QuerySet). We override
    # ``filter_queryset`` below to materialise the per-action class
    # onto the instance attribute right before DRF's filter chain
    # runs, so the backend actually sees it.
    _action_filter_map: dict[str, type] = {
        "favourite_products": ProductFavouriteFilter,
        "orders": OrderFilter,
        "product_reviews": ProductReviewFilter,
        "addresses": UserAddressFilter,
        "blog_post_comments": BlogCommentFilter,
        "liked_blog_posts": BlogPostFilter,
        "notifications": NotificationUserFilter,
        "subscriptions": UserSubscriptionFilter,
    }

    def get_filterset_class(self):
        return self._action_filter_map.get(self.action or "", UserAccountFilter)

    def filter_queryset(self, queryset):
        self.filterset_class = self.get_filterset_class()
        return super().filter_queryset(queryset)

    def _get_checked_user(self):
        """Get the target user with ownership/admin permission check."""
        user = get_object_or_404(User, id=self.kwargs["pk"])
        self.check_object_permissions(self.request, user)
        return user

    def get_queryset(self):
        match self.action:
            case "favourite_products":
                from product.models.favourite import ProductFavourite

                user = self._get_checked_user()
                queryset = ProductFavourite.objects.for_list().filter(user=user)
            case "orders":
                from order.models.order import Order

                user = self._get_checked_user()
                queryset = Order.objects.for_list().filter(user=user)
            case "product_reviews":
                from product.models.review import ProductReview

                user = self._get_checked_user()
                queryset = ProductReview.objects.for_list().filter(user=user)
            case "addresses":
                from user.models.address import UserAddress

                user = self._get_checked_user()
                queryset = UserAddress.objects.for_list().filter(user=user)
            case "blog_post_comments":
                from blog.models.comment import BlogComment

                user = self._get_checked_user()
                queryset = BlogComment.objects.for_list().filter(user=user)
            case "liked_blog_posts":
                from blog.models.post import BlogPost

                user = self._get_checked_user()
                queryset = BlogPost.objects.for_list().filter(likes=user)
            case "notifications":
                from notification.models import NotificationUser

                user = self._get_checked_user()
                queryset = NotificationUser.objects.for_list().filter(user=user)
            case "subscriptions":
                user = self._get_checked_user()
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
        request_serializer.is_valid(raise_exception=True)

        new_username = request_serializer.validated_data["username"]

        try:
            user.username = new_username
            user.save(update_fields=["username"])
        except IntegrityError:
            return Response(
                {"detail": _("Username already taken.")},
                status=status.HTTP_409_CONFLICT,
            )

        response_data = {"detail": _("Username updated successfully.")}

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(response_data)
        return Response(response_serializer.data, status=status.HTTP_200_OK)

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

    @action(detail=True, methods=["GET"])
    def data_exports(self, request, pk=None):
        """List this user's recent GDPR export jobs (paginated)."""
        from user.models.data_export import UserDataExport

        self.ordering_fields = []
        self.ordering = []
        self.search_fields = []

        user = self.get_object()
        queryset = UserDataExport.objects.filter(user=user).order_by(
            "-created_at"
        )

        return self.paginate_and_serialize(
            queryset, request, serializer_class=UserDataExportSerializer
        )

    @action(detail=True, methods=["POST"])
    def request_data_export(self, request, pk=None):
        """Queue a data-export job for this user.

        Rate-limited to one PENDING/PROCESSING export at a time per
        user so a double-click doesn't spawn duplicate jobs. The
        Celery task is idempotent per ``UserDataExport`` row anyway,
        but the UX point is to show the in-flight one not a stack.
        """
        from user.models.data_export import UserDataExport
        from user.services.gdpr import create_export_request
        from user.tasks import export_user_data_task

        user = self.get_object()

        existing = (
            UserDataExport.objects.filter(
                user=user,
                status__in=[
                    UserDataExport.Status.PENDING,
                    UserDataExport.Status.PROCESSING,
                ],
            )
            .order_by("-created_at")
            .first()
        )
        if existing is not None:
            serializer = UserDataExportSerializer(
                existing, context={"request": request}
            )
            return Response(serializer.data, status=status.HTTP_202_ACCEPTED)

        export = create_export_request(user)
        export_user_data_task.delay(export.id)

        serializer = UserDataExportSerializer(
            export, context={"request": request}
        )
        return Response(serializer.data, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["POST"])
    def delete_account(self, request, pk=None):
        """Right-to-erasure endpoint.

        The caller must POST ``{"confirmation": "DELETE"}``. The
        actual scrub runs in ``delete_user_account_task``; we respond
        immediately and the session is invalidated on the next
        request when the User row is gone.
        """
        from user.tasks import delete_user_account_task

        user = self.get_object()

        if request.user != user and not request.user.is_staff:
            return Response(
                {"detail": _("You can only delete your own account.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        req_serializer = DeleteAccountRequestSerializer(data=request.data)
        req_serializer.is_valid(raise_exception=True)

        delete_user_account_task.delay(user.id)

        return Response(
            {
                "detail": _(
                    "Your account is being deleted. You will be logged out "
                    "shortly. Orders are retained in anonymised form for "
                    "tax/accounting purposes."
                )
            },
            status=status.HTTP_202_ACCEPTED,
        )
