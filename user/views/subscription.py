import logging

from django.contrib.auth import get_user_model
from django.core import signing
from django.db import connection, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from rest_framework.views import APIView

from core.api.permissions import (
    IsOwnerOrAdmin,
    StoreStaffModelPermissions,
)
from core.api.serializers import DetailSerializer, ErrorResponseSerializer
from core.api.throttling import NewsletterSubscribeThrottle
from core.api.views import BaseModelViewSet
from core.client_ip import trusted_client_ip
from core.utils.i18n import resolve_request_language
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
from user.models.subscription import (
    ConfirmOutcome,
    SubscriptionTopic,
    UserSubscription,
)
from user.serializers.subscription import (
    BulkSubscriptionResultSerializer,
    BulkSubscriptionSerializer,
    NewsletterAvailabilitySerializer,
    NewsletterSubscribeSerializer,
    SubscriptionTopicDetailSerializer,
    SubscriptionTopicSerializer,
    SubscriptionTopicWriteSerializer,
    UserSubscriptionDetailSerializer,
    UserSubscriptionSerializer,
    UserSubscriptionWriteSerializer,
)
from user.services.subscription import SubscribeOutcome, subscribe_account
from user.utils.subscription import (
    UNSUBSCRIBE_MAX_AGE,
    UNSUBSCRIBE_SALT,
)

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
        paginated=False,
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

    def get_permissions(self):
        from tenant.permissions import (
            IsNewsletterEnabled,
        )

        # Merchant feature gate always fires first (404 when
        # disabled). The token-based confirm/unsubscribe views below
        # are deliberately NOT gated: links in already-sent emails
        # must keep working after the feature is turned off.
        #
        # A topic is STORE configuration, not user data. `IsAuthenticated`
        # alone let any registered shopper POST, PUT, PATCH and DELETE
        # them — and `UserSubscription.topic` is `on_delete=CASCADE`, so
        # one DELETE took the store's entire subscriber list with it,
        # unrecoverably. `IsNewsletterEnabled` could not help: it is a
        # FEATURE gate that 404s when the merchant switches the feature
        # off and otherwise returns True, contributing no authorization.
        # Writes take the same predicate every other store-configuration
        # viewset uses.
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [IsNewsletterEnabled(), StoreStaffModelPermissions()]
        return [IsNewsletterEnabled(), *super().get_permissions()]

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
        result = subscribe_account(
            request.user, topic, language=resolve_request_language(request)
        )

        if result.outcome == SubscribeOutcome.ALREADY_ACTIVE:
            return Response(
                {"detail": _("Already subscribed to this topic.")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if result.outcome == SubscribeOutcome.ALREADY_PENDING:
            return Response(
                {"detail": _("Subscription pending confirmation.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(result.subscription)
        return Response(
            response_serializer.data,
            status=(
                status.HTTP_201_CREATED
                if result.created
                else status.HTTP_200_OK
            ),
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
            200: BulkSubscriptionResultSerializer,
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
        responses={
            200: UserSubscriptionDetailSerializer,
            400: ErrorResponseSerializer,
            410: ErrorResponseSerializer,
        },
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

    def get_permissions(self):
        from tenant.permissions import (
            IsNewsletterEnabled,
        )

        return [IsNewsletterEnabled(), *super().get_permissions()]

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
                        result = subscribe_account(
                            user,
                            topic,
                            language=resolve_request_language(request),
                        )
                        if result.outcome in (
                            SubscribeOutcome.SUBSCRIBED,
                            SubscribeOutcome.PENDING_CONFIRMATION,
                        ):
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

                except Exception:
                    # Log the real error server-side; exception text must
                    # not reach the response body (CodeQL
                    # py/stack-trace-exposure).
                    logger.exception(
                        "Subscription bulk_update failed | user=%s topic=%s",
                        user.id,
                        topic.id,
                    )
                    results["failed"].append(
                        {
                            "topic": topic.name,
                            "error": _("Unable to update this subscription."),
                        }
                    )

        return Response(results)

    @action(
        detail=True,
        methods=["POST"],
        permission_classes=[IsAuthenticated],
    )
    def confirm(self, request, pk=None):
        """Authenticated confirm — the owner submits the token from their UI.

        The same rule as the emailed link (``ConfirmSubscriptionByTokenView``)
        because it is the same call: ``UserSubscription.confirm``.
        """
        subscription = self.get_object()
        token = request.data.get("token")
        outcome = subscription.confirm(
            token if isinstance(token, str) else "",
            ip=trusted_client_ip(request),
        )
        if outcome == ConfirmOutcome.EXPIRED:
            return Response(
                {"detail": CONFIRMATION_EXPIRED_MESSAGE},
                status=status.HTTP_410_GONE,
            )
        if outcome == ConfirmOutcome.INVALID:
            return Response(
                {"detail": _("Invalid confirmation token.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(
            subscription, context=self.get_serializer_context()
        )
        return Response(response_serializer.data)


CONFIRMATION_EXPIRED_MESSAGE = _(
    "This confirmation link has expired. Please subscribe again to get a "
    "new one."
)


class NewsletterSubscribeView(APIView):
    """The storefront newsletter form: subscribe an email address to the
    tenant's default newsletter topic, with double opt-in.

    Anonymous by design, so it must not tell anyone which addresses it
    knows — not in its answer, and not in its TIMING. The request path is
    therefore the same for every address: the merchant gate, request
    validation, resolving the default topic (404 when there is none —
    a fact about the store, not the address), and a snapshot of the
    consent. Then it ALWAYS enqueues exactly one task and answers 202.
    Every address-dependent step — looking the row up, attaching a
    verified account, (re-)arming with the resend cooldown, the insert
    race, the email — happens in ``user.tasks.subscribe_to_newsletter_task``.
    A confirmed address that skipped the save and the broker publish
    answered measurably faster than a new one; now nothing differs.

    The consent snapshot is taken HERE, because it describes this
    request: the exact sentence the visitor agreed to (supplied by the
    storefront server, which renders it), the visitor's IP as far as it
    can be proven (``core.client_ip.trusted_client_ip``), the user agent,
    the request language and the time.
    """

    permission_classes = [AllowAny]
    throttle_classes = [
        AnonRateThrottle,
        UserRateThrottle,
        NewsletterSubscribeThrottle,
    ]

    def get_permissions(self):
        from tenant.permissions import IsNewsletterEnabled

        # The merchant gate first (404 when off), then AllowAny: the
        # default IsAuthenticatedOrReadOnly would refuse the anonymous
        # POST this endpoint exists for.
        return [IsNewsletterEnabled(), *super().get_permissions()]

    def get_throttles(self):
        # The scoped budget bounds SUBMISSIONS. The availability read is
        # made on every render of the band and must not spend it.
        if self.request.method == "GET":
            return [AnonRateThrottle(), UserRateThrottle()]
        return super().get_throttles()

    @extend_schema(
        operation_id="getNewsletterAvailability",
        summary=_("Whether the newsletter form can be offered"),
        description=_(
            "True when the store has a default newsletter topic, i.e. "
            "when a newsletter form submission can be honoured."
        ),
        tags=["User Subscriptions"],
        responses={
            200: NewsletterAvailabilitySerializer,
            404: ErrorResponseSerializer,
        },
    )
    def get(self, request):
        available = SubscriptionTopic.objects.default_newsletter().exists()
        return Response({"available": available})

    @extend_schema(
        operation_id="subscribeToNewsletter",
        summary=_("Subscribe an email address to the newsletter"),
        description=_(
            "Subscribes an email address to the store's default "
            "newsletter topic. Always answers 202: a confirmation email "
            "is sent unless the address is already confirmed or one was "
            "sent in the last 10 minutes."
        ),
        tags=["User Subscriptions"],
        request=NewsletterSubscribeSerializer,
        responses={
            202: DetailSerializer,
            400: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            429: ErrorResponseSerializer,
        },
    )
    def post(self, request):
        serializer = NewsletterSubscribeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        topic = SubscriptionTopic.objects.default_newsletter().first()
        if topic is None:
            return Response(
                {"detail": _("This store has no newsletter to subscribe to.")},
                status=status.HTTP_404_NOT_FOUND,
            )

        from tenant.celery import dispatch_on_commit
        from user.tasks import subscribe_to_newsletter_task

        dispatch_on_commit(
            subscribe_to_newsletter_task,
            kwargs={
                "topic_id": topic.pk,
                "email": serializer.validated_data["email"],
                "consent_text": serializer.validated_data["consent_text"],
                "consent_ip": trusted_client_ip(request),
                "consent_user_agent": request.headers.get("User-Agent", "")[
                    :512
                ],
                "language": resolve_request_language(request),
                "consented_at": timezone.now().isoformat(),
            },
        )
        return Response(
            {
                "detail": _(
                    "Thank you. Check your inbox for a link to confirm "
                    "your subscription."
                )
            },
            status=status.HTTP_202_ACCEPTED,
        )


class ConfirmSubscriptionByTokenView(APIView):
    """Public endpoint behind the storefront confirmation page.

    The confirmation token itself is 64 chars of random entropy
    (sufficient authorization); no login required. POST only: the
    emailed link opens a storefront page whose button POSTs here, so a
    mail link scanner that prefetches the link confirms nothing. What
    counts as a valid confirmation — PENDING, matching token, link no
    older than ``CONFIRMATION_TTL`` (else 410) — is
    ``UserSubscription.confirm``, the same rule the signed-in confirm
    applies.

    Deliberately not gated on ``NEWSLETTER_ENABLED``: links in emails
    already sent must keep working after the feature is turned off.
    Tokens are rows in the tenant's own schema, so one issued by another
    store is simply unknown here.
    """

    permission_classes = []
    authentication_classes = []

    class ConfirmResponseSerializer(serializers.Serializer):
        status = serializers.CharField()
        topic = serializers.CharField(required=False)

    serializer_class = ConfirmResponseSerializer

    @extend_schema(
        operation_id="confirmSubscriptionByToken",
        summary=_("Confirm a pending subscription via email token"),
        tags=["User Subscriptions"],
        request=None,
        responses={
            200: ConfirmResponseSerializer,
            400: ErrorResponseSerializer,
            410: ErrorResponseSerializer,
        },
    )
    def post(self, request, token: str):
        subscription = (
            UserSubscription.objects.select_related("user", "topic")
            .filter(
                confirmation_token=token,
                status=UserSubscription.SubscriptionStatus.PENDING,
            )
            .first()
        )
        outcome = (
            subscription.confirm(token, ip=trusted_client_ip(request))
            if subscription is not None
            else ConfirmOutcome.INVALID
        )
        if outcome == ConfirmOutcome.EXPIRED:
            return Response(
                {"detail": CONFIRMATION_EXPIRED_MESSAGE},
                status=status.HTTP_410_GONE,
            )
        if outcome == ConfirmOutcome.INVALID or subscription is None:
            return Response(
                {"detail": _("Invalid confirmation link.")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {"status": "confirmed", "topic": subscription.topic.name},
            status=status.HTTP_200_OK,
        )


class UnsubscribeResponseSerializer(serializers.Serializer):
    message = serializers.CharField()
    topic = serializers.CharField(required=False)
    user_email = serializers.EmailField(required=False)
    topic_slug = serializers.CharField(required=False)
    count = serializers.IntegerField(required=False)
    error = serializers.CharField(required=False)


def _validate_unsubscribe_token(token: str):
    """Resolve an unsubscribe token to the subscriptions it may end.

    Returns ``(queryset, None)`` or ``(None, error)``. The token is a
    ``django.core.signing`` value carrying the owning schema plus either
    an account pk (``pk`` — every subscription of that account) or a
    guest subscription's uuid (``sid`` — that one row); see
    ``user.utils.subscription._make_unsubscribe_token`` and
    ``_make_guest_unsubscribe_token``. It is tamper-proof and does not
    depend on the user's password/last_login, so the link keeps working
    after logins/password changes — unlike the old password-reset
    generator.
    """
    try:
        payload = signing.loads(
            token, salt=UNSUBSCRIBE_SALT, max_age=UNSUBSCRIBE_MAX_AGE
        )
    except signing.SignatureExpired:
        logger.warning(
            "unsubscribe: token expired (older than %s) — link stale",
            UNSUBSCRIBE_MAX_AGE,
        )
        return None, _("Invalid or expired unsubscribe link")
    except signing.BadSignature:
        # Log so broken/forged links surface in observability without
        # leaking detail to the caller (POST replies are silent 200 per
        # RFC 8058; GET replies still 400 with a generic error message).
        logger.warning(
            "unsubscribe: bad signature — link is malformed or tampered"
        )
        return None, _("Invalid unsubscribe link")

    # Schema scoping: SECRET_KEY is global and pk sequences repeat per
    # schema, so a cryptographically valid token from another tenant's
    # domain still verifies. Reject anything not minted for THIS schema
    # with the same generic error a forged token gets.
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != connection.schema_name
    ):
        logger.warning(
            "unsubscribe: token schema mismatch (minted for %r, "
            "presented on %r)",
            payload.get("schema") if isinstance(payload, dict) else None,
            connection.schema_name,
        )
        return None, _("Invalid unsubscribe link")

    if "sid" in payload:
        # A guest row — or one since claimed by an account, whose
        # recipient the link was sent to all the same.
        subscriptions = UserSubscription.objects.filter(uuid=payload["sid"])
        if not subscriptions.exists():
            logger.warning(
                "unsubscribe: signed subscription %s no longer exists",
                payload["sid"],
            )
            return None, _("Invalid unsubscribe link")
        return subscriptions, None

    user_pk = payload.get("pk")
    if user_pk is None or not User.objects.filter(pk=user_pk).exists():
        logger.warning(
            "unsubscribe: signed user_id=%s no longer exists", user_pk
        )
        return None, _("Invalid unsubscribe link")
    return UserSubscription.objects.filter(user_id=user_pk), None


def _apply_unsubscribe(subscriptions, topic_slug: str | None):
    qs = subscriptions.filter(
        status=UserSubscription.SubscriptionStatus.ACTIVE,
    )
    if topic_slug:
        qs = qs.filter(topic__slug=topic_slug)
    count = 0
    topic_name = None
    recipient = None
    for subscription in qs.select_related("topic", "user"):
        subscription.unsubscribe()
        topic_name = subscription.topic.name
        recipient = subscription.recipient_email
        count += 1
    return count, topic_name, recipient


def _unsubscribe_get_response(token: str, topic_slug: str | None):
    subscriptions, error = _validate_unsubscribe_token(token)
    if error is not None:
        return Response(
            {"error": str(error)}, status=status.HTTP_400_BAD_REQUEST
        )
    count, topic_name, recipient = _apply_unsubscribe(subscriptions, topic_slug)
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
            "user_email": recipient,
            "count": count,
        }
    )


def _unsubscribe_post_response(token: str, topic_slug: str | None):
    subscriptions, _err = _validate_unsubscribe_token(token)
    if subscriptions is not None:
        _apply_unsubscribe(subscriptions, topic_slug)
    # RFC 8058: always 200 to avoid leaking token validity to scanners.
    return Response(status=status.HTTP_200_OK)


class UnsubscribeTopicView(APIView):
    """Topic-scoped unsubscribe: `/unsubscribe/<token>/<topic_slug>`.

    Used by marketing emails with a specific List-ID (newsletter etc.).
    """

    permission_classes = []
    authentication_classes = []
    serializer_class = UnsubscribeResponseSerializer

    @extend_schema(
        operation_id="unsubscribeFromTopicViaLink",
        summary=_("Unsubscribe from a topic via email link (GET)"),
        tags=["User Subscriptions"],
        responses={
            200: UnsubscribeResponseSerializer,
            400: ErrorResponseSerializer,
        },
    )
    def get(self, request, token: str, topic_slug: str):
        return _unsubscribe_get_response(token, topic_slug)

    @extend_schema(
        operation_id="unsubscribeFromTopicOneClick",
        summary=_("RFC 8058 one-click unsubscribe from a topic"),
        description=_(
            "Invoked by mail clients honoring List-Unsubscribe-Post=One-Click. "
            "Returns 200 OK regardless of token validity (silent per RFC 8058)."
        ),
        tags=["User Subscriptions"],
        request=None,
        responses={200: None},
    )
    def post(self, request, token: str, topic_slug: str):
        return _unsubscribe_post_response(token, topic_slug)


class UnsubscribeAllView(APIView):
    """Non-topic unsubscribe: `/unsubscribe/<token>`.

    Unsubscribes from ALL active subscriptions — used by emails without a
    topic binding such as re-engagement and abandoned-cart.
    """

    permission_classes = []
    authentication_classes = []
    serializer_class = UnsubscribeResponseSerializer

    @extend_schema(
        operation_id="unsubscribeFromAllViaLink",
        summary=_("Unsubscribe from all topics via email link (GET)"),
        tags=["User Subscriptions"],
        responses={
            200: UnsubscribeResponseSerializer,
            400: ErrorResponseSerializer,
        },
    )
    def get(self, request, token: str):
        return _unsubscribe_get_response(token, None)

    @extend_schema(
        operation_id="unsubscribeFromAllOneClick",
        summary=_("RFC 8058 one-click unsubscribe from all topics"),
        description=_(
            "Invoked by mail clients honoring List-Unsubscribe-Post=One-Click. "
            "Returns 200 OK regardless of token validity (silent per RFC 8058)."
        ),
        tags=["User Subscriptions"],
        request=None,
        responses={200: None},
    )
    def post(self, request, token: str):
        return _unsubscribe_post_response(token, None)
