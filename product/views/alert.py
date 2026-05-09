from __future__ import annotations

from django.db import IntegrityError
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_view
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from core.api.serializers import ErrorResponseSerializer
from core.api.views import BaseModelViewSet
from core.utils.serializers import (
    ActionConfig,
    SerializersConfig,
    create_schema_view_config,
)
from product.models.alert import ProductAlert, ProductAlertKind
from product.models.product import Product
from product.serializers.alert import ProductAlertSerializer

serializers_config: SerializersConfig = {
    "list": ActionConfig(response=ProductAlertSerializer),
    "retrieve": ActionConfig(response=ProductAlertSerializer),
    "create": ActionConfig(
        request=ProductAlertSerializer,
        response=ProductAlertSerializer,
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

    Authenticated users manage their own alerts and never see others';
    anonymous users can create an alert by providing an email, which
    receives the single-shot notification when the product fires.
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
        if self.action == "create":
            return [AllowAny()]
        return [IsAuthenticated()]

    def get_queryset(self):
        user = getattr(self.request, "user", None)
        if user is None or not user.is_authenticated:
            return ProductAlert.objects.none()
        if user.is_staff:
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
                    {"detail": _("Product not found.")},
                    status=status.HTTP_404_NOT_FOUND,
                )
            if not product.price_drop_alerts_enabled:
                return Response(
                    {
                        "detail": _(
                            "Price-drop alerts are not enabled for this product."
                        )
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

        if user is not None:
            validated["user"] = user
            validated["email"] = ""
        else:
            email = (validated.get("email") or "").strip()
            if not email:
                return Response(
                    {
                        "detail": _(
                            "An email address is required for guest alerts."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            validated["email"] = email
            validated["user"] = None

        try:
            alert = ProductAlert.objects.create(**validated)
        except IntegrityError:
            return Response(
                {
                    "detail": _(
                        "You already have an active alert of this kind for this product."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        response_serializer_class = self.get_response_serializer()
        response = response_serializer_class(
            alert, context=self.get_serializer_context()
        )
        return Response(response.data, status=status.HTTP_201_CREATED)
