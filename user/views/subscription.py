import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.db import transaction
from django.utils.crypto import get_random_string
from django.utils.http import urlsafe_base64_decode
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import serializers, status
from rest_framework.decorators import action

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api.permissions import IsOwnerOrAdmin
from core.api.serializers import ErrorResponseSerializer, DetailSerializer
from core.api.views import BaseModelViewSet
from core.utils.serializers import (
    ActionConfig,
    SerializersConfig,
    create_schema_view_config,
    crud_config,
)
from user.filters.subscription import (
    SubscriptionTopicFilter,
    UserSubscriptionFilter,
)
from user.models.subscription import SubscriptionTopic, UserSubscription
from user.serializers.subscription import (
    BulkSubscriptionSerializer,
    SubscriptionTopicDetailSerializer,
    SubscriptionTopicSerializer,
    SubscriptionTopicWriteSerializer,
    UserSubscriptionDetailSerializer,
    UserSubscriptionSerializer,
    UserSubscriptionWriteSerializer,
)
from user.utils.subscription import send_subscription_confirmation

logger = logging.getLogger(__name__)

User = get_user_model()

subscription_topic_config: SerializersConfig = {
    **crud_config(
        list=SubscriptionTopicSerializer,
        detail=SubscriptionTopicDetailSerializer,
        write=SubscriptionTopicWriteSerializer,
    ),
    "my_subscriptions": ActionConfig(
        response=SubscriptionTopicSerializer,
        many=True,
        operation_id="getMySubscriptionTopics",
        summary=_("Get my subscriptions"),
        description=_(
            "Get the current user's subscribed and available subscription topics."
        ),
        tags=["Subscription Topics"],
        responses={
            200: {
                "type": "object",
                "properties": {
                    "subscribed": {
                        "type": "array",
                        "items": {
                            "$ref": "#/components/schemas/SubscriptionTopic"
                        },
                    },
                    "available": {
                        "type": "array",
                        "items": {
                            "$ref": "#/components/schemas/SubscriptionTopic"
                        },
                    },
                },
            },
        },
    ),
    "subscribe": ActionConfig(
        response=UserSubscriptionSerializer,
        operation_id="subscribeToTopic",
        summary=_("Subscribe to a topic"),
        description=_(
            "Subscribe the current user to a specific subscription topic."
        ),
        tags=["Subscription Topics"],
        responses={
            201: UserSubscriptionSerializer,
        },
    ),
    "unsubscribe": ActionConfig(
        response=DetailSerializer,
        operation_id="unsubscribeFromTopic",
        summary=_("Unsubscribe from a topic"),
        description=_(
            "Unsubscribe the current user from a specific subscription topic."
        ),
        tags=["Subscription Topics"],
    ),
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=SubscriptionTopic,
        display_config={
            "tag": "Subscription Topics",
        },
        serializers_config=subscription_topic_config,
        error_serializer=ErrorResponseSerializer,
    )
)
class SubscriptionTopicViewSet(BaseModelViewSet):
    queryset = SubscriptionTopic.objects.filter(is_active=True)
    serializers_config = subscription_topic_config
    permission_classes = [IsAuthenticated]
    filterset_class = SubscriptionTopicFilter
    ordering_fields = ["category", "created_at", "updated_at", "slug"]
    ordering = ["category"]
    search_fields = ["translations__name", "translations__description", "slug"]

    def get_queryset(self):
        return (
            SubscriptionTopic.objects.for_list()
            .filter(is_active=True)
            .distinct()
        )

    @action(detail=False, methods=["GET"])
    def my_subscriptions(self, request):
        user = request.user

        subscribed_topics = SubscriptionTopic.objects.filter(
            subscribers__user=user,
            subscribers__status=UserSubscription.SubscriptionStatus.ACTIVE,
            is_active=True,
        ).distinct()

        available_topics = SubscriptionTopic.objects.filter(
            is_active=True
        ).exclude(id__in=subscribed_topics.values_list("id", flat=True))

        response_serializer_class = self.get_response_serializer()
        subscribed_data = response_serializer_class(
            subscribed_topics, many=True, context=self.get_serializer_context()
        ).data
        available_data = response_serializer_class(
            available_topics, many=True, context=self.get_serializer_context()
        ).data

        response_data = {
            "subscribed": subscribed_data,
            "available": available_data,
        }

        return Response(response_data)

    @action(detail=True, methods=["POST"])
    def subscribe(self, request, pk=None):
        topic = self.get_object()
        user = request.user

        existing = UserSubscription.objects.filter(
            user=user, topic=topic
        ).first()

        if existing:
            if existing.status == UserSubscription.SubscriptionStatus.ACTIVE:
                return Response(
                    {"detail": _("Already subscribed to this topic.")},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            elif existing.status == UserSubscription.SubscriptionStatus.PENDING:
                return Response(
                    {"detail": _("Subscription pending confirmation.")},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            else:
                existing.status = UserSubscription.SubscriptionStatus.ACTIVE
                existing.unsubscribed_at = None
                existing.save()

                response_serializer_class = self.get_response_serializer()
                response_serializer = response_serializer_class(existing)
                return Response(response_serializer.data)

        subscription_data = {
            "user": user,
            "topic": topic,
            "status": (
                UserSubscription.SubscriptionStatus.PENDING
                if topic.requires_confirmation
                else UserSubscription.SubscriptionStatus.ACTIVE
            ),
        }

        if topic.requires_confirmation:
            subscription_data["confirmation_token"] = get_random_string(64)

        subscription = UserSubscription.objects.create(**subscription_data)

        if topic.requires_confirmation:
            success = send_subscription_confirmation(subscription, user)
            if not success:
                logger.warning(
                    f"Failed to send confirmation email for subscription {subscription.id}"
                )

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(subscription)
        return Response(
            response_serializer.data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["POST"])
    def unsubscribe(self, request, pk=None):
        topic = self.get_object()
        user = request.user

        try:
            subscription = UserSubscription.objects.get(
                user=user,
                topic=topic,
                status=UserSubscription.SubscriptionStatus.ACTIVE,
            )
            subscription.unsubscribe()
            return Response(
                {"detail": _("Successfully unsubscribed.")},
                status=status.HTTP_200_OK,
            )
        except UserSubscription.DoesNotExist:
            return Response(
                {"detail": _("You are not subscribed to this topic.")},
                status=status.HTTP_400_BAD_REQUEST,
            )


user_subscription_config: SerializersConfig = {
    **crud_config(
        list=UserSubscriptionSerializer,
        detail=UserSubscriptionDetailSerializer,
        write=UserSubscriptionWriteSerializer,
    ),
    "bulk_update": ActionConfig(
        request=BulkSubscriptionSerializer,
        operation_id="bulkUpdateUserSubscriptions",
        summary=_("Bulk update user subscriptions"),
        description=_("Subscribe or unsubscribe from multiple topics at once."),
        tags=["User Subscriptions"],
        responses={
            200: None,
        },
    ),
    "confirm": ActionConfig(
        response=UserSubscriptionDetailSerializer,
        operation_id="confirmUserSubscription",
        summary=_("Confirm a user subscription"),
        description=_(
            "Confirm a pending subscription using the confirmation token."
        ),
        tags=["User Subscriptions"],
    ),
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=UserSubscription,
        display_config={
            "tag": "User Subscription",
        },
        serializers_config=user_subscription_config,
        error_serializer=ErrorResponseSerializer,
    )
)
class UserSubscriptionViewSet(BaseModelViewSet):
    queryset = UserSubscription.objects.none()
    serializers_config = user_subscription_config

    permission_classes = [IsOwnerOrAdmin]
    filterset_class = UserSubscriptionFilter
    ordering_fields = [
        "subscribed_at",
        "unsubscribed_at",
        "created_at",
        "updated_at",
        "status",
        "topic__category",
    ]
    ordering = ["-subscribed_at"]
    search_fields = [
        "topic__translations__name",
        "topic__translations__description",
        "topic__slug",
    ]

    def get_queryset(self):
        return UserSubscription.objects.for_list().filter(
            user=self.request.user
        )

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=False, methods=["POST"])
    def bulk_update(self, request):
        request_serializer_class = self.get_request_serializer()
        request_serializer = request_serializer_class(data=request.data)
        request_serializer.is_valid(raise_exception=True)

        topic_ids = request_serializer.validated_data["topic_ids"]
        action = request_serializer.validated_data["action"]
        user = request.user

        existing_topics = list(
            SubscriptionTopic.objects.filter(id__in=topic_ids, is_active=True)
        )

        if len(existing_topics) != len(topic_ids):
            existing_ids = [topic.id for topic in existing_topics]
            invalid_ids = [tid for tid in topic_ids if tid not in existing_ids]
            return Response(
                {
                    "detail": _("Invalid or inactive topic IDs: {}").format(
                        invalid_ids
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        results = {"success": [], "failed": [], "already_processed": []}

        with transaction.atomic():
            for topic in existing_topics:
                try:
                    if action == "subscribe":
                        subscription, created = (
                            UserSubscription.objects.get_or_create(
                                user=user,
                                topic=topic,
                                defaults={
                                    "status": UserSubscription.SubscriptionStatus.ACTIVE
                                },
                            )
                        )

                        if created:
                            results["success"].append(topic.name)
                        elif (
                            subscription.status
                            != UserSubscription.SubscriptionStatus.ACTIVE
                        ):
                            subscription.status = (
                                UserSubscription.SubscriptionStatus.ACTIVE
                            )
                            subscription.unsubscribed_at = None
                            subscription.save()
                            results["success"].append(topic.name)
                        else:
                            results["already_processed"].append(topic.name)

                    else:
                        try:
                            subscription = UserSubscription.objects.get(
                                user=user,
                                topic=topic,
                                status=UserSubscription.SubscriptionStatus.ACTIVE,
                            )
                            subscription.unsubscribe()
                            results["success"].append(topic.name)
                        except UserSubscription.DoesNotExist:
                            results["already_processed"].append(topic.name)

                except Exception as e:
                    results["failed"].append(
                        {"topic": topic.name, "error": str(e)}
                    )

        return Response(results)

    @action(
        detail=True,
        methods=["POST"],
        permission_classes=[IsAuthenticated],
    )
    def confirm(self, request, pk=None):
        """Authenticated confirm — the owner submits the token from their UI.

        A separate public endpoint `ConfirmSubscriptionByTokenView` handles
        the unauthenticated email-link case.
        """
        try:
            subscription = self.get_object()
            token = request.data.get("token")

            if (
                subscription.status
                != UserSubscription.SubscriptionStatus.PENDING
            ):
                return Response(
                    {"detail": _("Subscription is not pending confirmation.")},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if subscription.confirmation_token != token:
                return Response(
                    {"detail": _("Invalid confirmation token.")},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            subscription.activate()

            response_serializer_class = self.get_response_serializer()
            response_serializer = response_serializer_class(
                subscription, context=self.get_serializer_context()
            )
            return Response(response_serializer.data)
        except Exception as e:
            logger.error("Error confirming subscription: %s", e, exc_info=True)
            return Response(
                {
                    "detail": _(
                        "An error occurred while confirming the subscription."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )


class ConfirmSubscriptionByTokenView(APIView):
    """Public endpoint for the confirmation link emailed to new subscribers.

    The confirmation token itself is 64 chars of random entropy (sufficient
    authorization); no login required. Accepts both GET (user clicks the
    link in a mail client) and POST (for programmatic confirmation).
    """

    permission_classes = []
    authentication_classes = []

    class ConfirmResponseSerializer(serializers.Serializer):
        status = serializers.CharField()
        topic = serializers.CharField(required=False)

    serializer_class = ConfirmResponseSerializer

    def _resolve(self, token: str):
        if not token:
            return None
        return (
            UserSubscription.objects.select_related("user", "topic")
            .filter(
                confirmation_token=token,
                status=UserSubscription.SubscriptionStatus.PENDING,
            )
            .first()
        )

    @extend_schema(
        operation_id="confirmSubscriptionByToken",
        summary=_("Confirm a pending subscription via email token"),
        tags=["User Subscriptions"],
        responses={
            200: ConfirmResponseSerializer,
            400: ErrorResponseSerializer,
        },
    )
    def get(self, request, token: str):
        return self._handle(token)

    def post(self, request, token: str):
        return self._handle(token)

    def _handle(self, token: str):
        subscription = self._resolve(token)
        if subscription is None:
            return Response(
                {"error": _("Invalid or expired confirmation link.")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        subscription.activate()
        return Response(
            {"status": "confirmed", "topic": subscription.topic.name},
            status=status.HTTP_200_OK,
        )


class UnsubscribeView(APIView):
    """GET (user click) + POST (RFC 8058 one-click) unsubscribe.

    `topic_slug` is optional: when omitted, the user is unsubscribed from
    ALL active subscriptions (used by non-topic marketing emails such as
    re-engagement and abandoned-cart).
    """

    permission_classes = []
    authentication_classes = []

    class UnsubscribeSerializer(serializers.Serializer):
        message = serializers.CharField()
        topic = serializers.CharField(required=False)
        user_email = serializers.EmailField(required=False)
        topic_slug = serializers.CharField(required=False)
        count = serializers.IntegerField(required=False)
        error = serializers.CharField(required=False)

    serializer_class = UnsubscribeSerializer

    def _validate_token(self, uidb64: str, token: str):
        try:
            uid = urlsafe_base64_decode(uidb64).decode()
            user = User.objects.get(pk=uid)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            return None, _("Invalid unsubscribe link")
        if not default_token_generator.check_token(user, token):
            return None, _("Invalid or expired unsubscribe link")
        return user, None

    def _apply(self, user, topic_slug: str | None):
        qs = UserSubscription.objects.filter(
            user=user,
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )
        if topic_slug:
            qs = qs.filter(topic__slug=topic_slug)
        count = 0
        topic_name = None
        for subscription in qs.select_related("topic"):
            subscription.unsubscribe()
            topic_name = subscription.topic.name
            count += 1
        return count, topic_name

    @extend_schema(
        operation_id="unsubscribeViaLink",
        summary=_("Unsubscribe via email link (GET)"),
        tags=["User Subscriptions"],
        responses={200: UnsubscribeSerializer, 400: ErrorResponseSerializer},
    )
    def get(
        self, request, uidb64: str, token: str, topic_slug: str | None = None
    ):
        return self._handle(request, uidb64, token, topic_slug)

    @extend_schema(
        operation_id="unsubscribeOneClick",
        summary=_("Unsubscribe via RFC 8058 one-click POST"),
        description=_(
            "Invoked by mail clients honoring List-Unsubscribe-Post=One-Click. "
            "Returns 200 OK on success or invalid token (silent per RFC 8058)."
        ),
        tags=["User Subscriptions"],
        request=None,
        responses={200: None},
    )
    def post(
        self, request, uidb64: str, token: str, topic_slug: str | None = None
    ):
        user, error = self._validate_token(uidb64, token)
        if user is not None:
            self._apply(user, topic_slug)
        # RFC 8058: always 200 to avoid leaking validity to scanners.
        return Response(status=status.HTTP_200_OK)

    def _handle(
        self,
        request,
        uidb64: str,
        token: str,
        topic_slug: str | None,
    ):
        user, error = self._validate_token(uidb64, token)
        if error is not None:
            return Response(
                {"error": str(error)}, status=status.HTTP_400_BAD_REQUEST
            )
        count, topic_name = self._apply(user, topic_slug)
        if count == 0:
            return Response(
                {
                    "message": str(_("Already unsubscribed")),
                    "topic_slug": topic_slug or "",
                }
            )
        return Response(
            {
                "message": str(_("Successfully unsubscribed")),
                "topic": topic_name or "",
                "user_email": user.email,
                "count": count,
            }
        )
