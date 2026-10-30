from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from rest_framework.views import APIView

from core.api.serializers import DetailSerializer, ErrorResponseSerializer
from core.api.throttling import ProductAlertCreateThrottle
from core.api.views import BaseModelViewSet
from core.utils.i18n import stated_request_language
from core.utils.serializers import (
    ActionConfig,
    SerializersConfig,
    create_schema_view_config,
)
from emails import optout
from emails.registry import registry
from product.alerts import (
    ALERT_EMAIL_TYPES,
    arm_customer_alert,
    confirm_guest_alert,
)
from product.models.alert import ProductAlert, ProductAlertKind
from product.models.product import Product
from product.serializers.alert import ProductAlertSerializer
from tenant.membership import is_store_staff
from user.models.subscription import ConfirmOutcome

serializers_config: SerializersConfig = {
    "list": ActionConfig(response=ProductAlertSerializer),
    "retrieve": ActionConfig(response=ProductAlertSerializer),
    "create": ActionConfig(
        request=ProductAlertSerializer,
        response=ProductAlertSerializer,
        responses={
            # A guest's request, whatever the address: confirm by email.
            status.HTTP_202_ACCEPTED: DetailSerializer,
            # A signed-in customer's ``alert_exists`` / ``email_opted_out``.
            status.HTTP_409_CONFLICT: ErrorResponseSerializer,
            status.HTTP_429_TOO_MANY_REQUESTS: ErrorResponseSerializer,
        },
    ),
    "destroy": ActionConfig(response=None),
}


@extend_schema_view(
    **create_schema_view_config(
        model_class=ProductAlert,
        display_config={"tag": "Product Alerts"},
        serializers_config=serializers_config,
        error_serializer=ErrorResponseSerializer,
    )
)
class ProductAlertViewSet(BaseModelViewSet):
    """CRUD for restock / price-drop subscriptions.

    Authenticated users manage their own alerts and never see others'.
    An anonymous visitor asks for one by giving an email: the address
    first confirms it from its inbox (double opt-in, ``product.alerts``),
    then receives the single-shot notification when the product fires.
    """

    queryset = ProductAlert.objects.all()
    serializers_config = serializers_config
    http_method_names = ["get", "post", "delete", "head", "options"]
    # ``product`` + ``kind`` + ``is_active`` power the PDP "am I already
    # subscribed?" lookup — the frontend queries this before opening the
    # Notify-Me modal so we render an "alert active" state instead of
    # letting the user submit and hit the 409 that uniqueness enforces.
    filterset_fields = ("product", "kind", "is_active")
    ordering_fields = ["id", "created_at", "notified_at"]
    ordering = ["-created_at"]

    def get_permissions(self):
        from tenant.permissions import (
            IsCatalogueEnabled,
            IsProductAlertsEnabled,
        )

        # Merchant feature gates always fire first (404 when disabled).
        gates = [IsCatalogueEnabled(), IsProductAlertsEnabled()]
        if self.action == "create":
            return [*gates, AllowAny()]
        return [*gates, IsAuthenticated()]

    def get_throttles(self):
        if self.action == "create":
            return [
                ProductAlertCreateThrottle(),
                AnonRateThrottle(),
                UserRateThrottle(),
            ]
        return super().get_throttles()

    def get_queryset(self):
        user = getattr(self.request, "user", None)
        if user is None or not user.is_authenticated:
            return ProductAlert.objects.none()
        if is_store_staff(user):
            return ProductAlert.objects.all()
        return ProductAlert.objects.filter(user=user)

    def create(self, request, *args, **kwargs):
        serializer_class = self.get_request_serializer()
        serializer = serializer_class(
            data=request.data, context=self.get_serializer_context()
        )
        serializer.is_valid(raise_exception=True)

        user = request.user if request.user.is_authenticated else None
        validated = dict(serializer.validated_data)

        # Guard: price-drop alerts require the feature to be enabled on the
        # product.  Reject new subscriptions with 403 when the flag is off.
        # Existing subscriptions (created before the flag was cleared) are
        # unaffected — only creation is blocked.
        if validated.get("kind") == ProductAlertKind.PRICE_DROP:
            product_id = (
                validated["product"].pk
                if hasattr(validated.get("product"), "pk")
                else validated.get("product")
            )
            try:
                product = Product.objects.only("price_drop_alerts_enabled").get(
                    pk=product_id
                )
            except Product.DoesNotExist:
                return Response(
                    {
                        "detail": _("Product not found."),
                        "code": "product_not_found",
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )
            if not product.price_drop_alerts_enabled:
                return Response(
                    {
                        "detail": _(
                            "Price-drop alerts are not enabled for this product."
                        ),
                        "code": "price_drop_alerts_unavailable",
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

        # The language the visitor is reading the storefront in, so the
        # alert email they asked for comes back in it. Unstated: the
        # email uses the store's.
        language = stated_request_language(request)
        if user is None:
            return self._request_guest_alert(validated, language)

        validated["user"] = user
        validated["email"] = ""
        validated["language_code"] = language

        # The mail is skipped for an address that opted out, so an alert
        # made for one could never be delivered: say so instead of a 201.
        # Only for the customer's own address: a guest's request answers
        # nothing about an address (``_request_guest_alert``).
        if optout.is_opted_out(
            user.email, registry.get(ALERT_EMAIL_TYPES[validated["kind"]])
        ):
            return Response(
                {
                    "detail": _(
                        "This address has unsubscribed from these emails, "
                        "so the alert could not be delivered."
                    ),
                    "code": "email_opted_out",
                },
                status=status.HTTP_409_CONFLICT,
            )

        alert = arm_customer_alert(validated)
        if alert is None:
            return Response(
                {
                    "detail": _(
                        "You already have an active alert of this kind for this product."
                    ),
                    "code": "alert_exists",
                },
                status=status.HTTP_409_CONFLICT,
            )

        response_serializer_class = self.get_response_serializer()
        response = response_serializer_class(
            alert, context=self.get_serializer_context()
        )
        return Response(response.data, status=status.HTTP_201_CREATED)

    def _request_guest_alert(self, validated, language):
        """A guest's request: the same 202 for every address.

        Whether the address is new, already holds the alert (live or
        pending) or opted out of its type must not show, neither in the
        answer nor in its cost: the request path is the same for every
        address and ALWAYS enqueues exactly one task, which does every
        address-dependent step
        (``product.tasks.request_guest_product_alert_task``).
        """
        email = (validated.get("email") or "").strip()
        if not email:
            return Response(
                {
                    "detail": _(
                        "An email address is required for guest alerts."
                    ),
                    "code": "alert_email_required",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        from product.tasks import request_guest_product_alert_task
        from tenant.celery import dispatch_on_commit

        target_price = validated.get("target_price")
        dispatch_on_commit(
            request_guest_product_alert_task,
            kwargs={
                "product_id": validated["product"].pk,
                "kind": validated["kind"],
                "email": email,
                "target_price": (
                    str(target_price.amount) if target_price else None
                ),
                "target_price_currency": (
                    str(target_price.currency) if target_price else ""
                ),
                "language": language,
            },
        )
        return Response(
            {
                "detail": _(
                    "Check your inbox for a link to confirm your alert."
                ),
            },
            status=status.HTTP_202_ACCEPTED,
        )


CONFIRMATION_EXPIRED_MESSAGE = _(
    "This confirmation link has expired. Please ask for the alert again to "
    "get a new one."
)


class ConfirmProductAlertResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    kind = serializers.ChoiceField(choices=ProductAlertKind.choices)


class ConfirmProductAlertByTokenView(APIView):
    """Public endpoint behind the storefront's alert confirmation page.

    The signed token is the authorisation; no login. POST only: the
    emailed link opens a storefront page whose button POSTs here, so a
    mail link scanner that prefetches the link confirms nothing. What a
    valid confirmation is (a pending guest alert of THIS store, a link no
    older than a week, else 410) is ``product.alerts.confirm_guest_alert``.

    Deliberately not gated on the alerts feature, like the newsletter's
    confirmation: links in emails already sent keep working, and the
    alert then waits for the feature like every other one.
    """

    permission_classes = []
    authentication_classes = []
    serializer_class = ConfirmProductAlertResponseSerializer

    @extend_schema(
        operation_id="confirmProductAlertByToken",
        summary=_("Confirm a guest's product alert via email token"),
        tags=["Product Alerts"],
        request=None,
        responses={
            200: ConfirmProductAlertResponseSerializer,
            400: ErrorResponseSerializer,
            410: ErrorResponseSerializer,
        },
    )
    def post(self, request, token: str):
        outcome, alert = confirm_guest_alert(token)
        if outcome == ConfirmOutcome.EXPIRED:
            return Response(
                {
                    "detail": CONFIRMATION_EXPIRED_MESSAGE,
                    "code": "confirmation_expired",
                },
                status=status.HTTP_410_GONE,
            )
        if outcome == ConfirmOutcome.INVALID or alert is None:
            return Response(
                {
                    "detail": _("Invalid confirmation link."),
                    "code": "confirmation_link_invalid",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {"status": "confirmed", "kind": alert.kind},
            status=status.HTTP_200_OK,
        )
