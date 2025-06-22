import logging

from django.conf import settings
from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from core.api.serializers import ErrorResponseSerializer
from core.filters.custom_filters import PascalSnakeCaseOrderingFilter
from core.utils.serializers import MultiSerializerMixin
from core.utils.views import cache_methods
from order.filters import OrderFilter
from order.models import Order
from order.serializers.payment import (
    PaymentStatusResponseSerializer,
    ProcessPaymentRequestSerializer,
    ProcessPaymentResponseSerializer,
    RefundRequestSerializer,
    RefundResponseSerializer,
)
from pay_way.models import PayWay
from pay_way.services import PayWayService

logger = logging.getLogger(__name__)


@extend_schema_view(
    process_payment=extend_schema(
        operation_id="processOrderPayment",
        summary=_("Process payment for an order"),
        description=_(
            "Initiates payment processing for the specified order using the provided payment method."
        ),
        tags=["Order Payment"],
        request=ProcessPaymentRequestSerializer,
        responses={
            200: ProcessPaymentResponseSerializer,
            400: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
    ),
    check_payment_status=extend_schema(
        operation_id="checkOrderPaymentStatus",
        summary=_("Check payment status"),
        description=_(
            "Retrieves the current payment status for the specified order."
        ),
        tags=["Order Payment"],
        responses={
            200: PaymentStatusResponseSerializer,
            400: ErrorResponseSerializer,
            403: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
    ),
    refund_payment=extend_schema(
        operation_id="refundOrderPayment",
        summary=_("Refund payment"),
        description=_(
            "Initiates a refund for the specified order. Requires admin permissions."
        ),
        tags=["Order Payment"],
        request=RefundRequestSerializer,
        responses={
            200: RefundResponseSerializer,
            400: ErrorResponseSerializer,
            500: ErrorResponseSerializer,
        },
    ),
)
@cache_methods(settings.DEFAULT_CACHE_TTL, methods=["check_payment_status"])
class OrderPaymentViewSet(MultiSerializerMixin, GenericViewSet):
    queryset = Order.objects.all()
    lookup_field = "pk"

    serializers = {
        "default": serializers.Serializer,
        "process_payment": ProcessPaymentRequestSerializer,
        "check_payment_status": PaymentStatusResponseSerializer,
        "refund_payment": RefundRequestSerializer,
    }

    response_serializers = {
        "process_payment": ProcessPaymentResponseSerializer,
        "check_payment_status": PaymentStatusResponseSerializer,
        "refund_payment": RefundResponseSerializer,
    }

    filter_backends = [
        DjangoFilterBackend,
        PascalSnakeCaseOrderingFilter,
        SearchFilter,
    ]
    filterset_class = OrderFilter
    ordering_fields = [
        "id",
        "created_at",
        "updated_at",
        "status",
        "payment_status",
    ]
    ordering = ["-created_at"]
    search_fields = [
        "first_name",
        "last_name",
        "email",
        "payment_id",
        "tracking_number",
    ]

    def get_permissions(self):
        if self.action == "refund_payment":
            self.permission_classes = [IsAdminUser]
        return super().get_permissions()

    def check_order_permission(self, request, order):
        if request.user.is_authenticated and request.user.is_staff:
            return True

        if request.user.is_authenticated and order.user_id == request.user.id:
            return True

        if not request.user.is_authenticated:
            if not order.user_id:
                request.session[f"order_{order.uuid}"] = True
                return True

            if request.session.get(f"order_{order.uuid}"):
                return True

        return False

    def _validate_process(
        self, request: HttpRequest, order: Order
    ) -> Response | None:
        if not self.check_order_permission(request, order):
            return Response(
                {
                    "detail": _(
                        "You do not have permission to process payment for this order."
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        if order.is_paid:
            return Response(
                {"detail": _("This order has already been paid.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        pay_way_id = request.data.get("pay_way_id")
        if not pay_way_id:
            return Response(
                {"detail": _("Payment method is required.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            pay_way = PayWay.objects.get(id=pay_way_id, active=True)
        except PayWay.DoesNotExist:
            return Response(
                {"detail": _("Invalid payment method.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        order.pay_way = pay_way
        order.save(update_fields=["pay_way"])
        return None

    @action(detail=True, methods=["post"])
    def process_payment(self, request: HttpRequest, pk: str) -> Response:
        order = get_object_or_404(Order, pk=pk)

        validation_error = self._validate_process(request, order)
        if validation_error:
            return validation_error

        payment_data = request.data.get("payment_data", {})
        try:
            success, response_data = PayWayService.process_payment(
                pay_way=order.pay_way, order=order, **payment_data
            )
            if not success:
                logger.error(
                    "Payment processing failed for order %s",
                    order.id,
                    extra={
                        "order_id": order.id,
                        "pay_way": order.pay_way.id,
                        "error": response_data.get("error", "Unknown error"),
                    },
                )
                return Response(
                    {
                        "detail": _("Payment processing failed."),
                        "error": response_data.get("error"),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            return Response(
                {
                    "detail": _("Payment processing initiated."),
                    "order_id": order.id,
                    "payment_status": order.payment_status,
                    "payment_id": response_data.get("payment_id", ""),
                    "requires_confirmation": order.pay_way.requires_confirmation,
                    "is_online_payment": order.pay_way.is_online_payment,
                    "provider_data": {
                        k: v
                        for k, v in response_data.items()
                        if k not in ["payment_id", "status", "error"]
                    },
                }
            )

        except Exception:
            logger.exception(
                "Exception during payment processing for order %s",
                order.id,
                extra={"order_id": order.id, "pay_way": order.pay_way.id},
            )
            return Response(
                {
                    "detail": _(
                        "An error occurred while processing the payment."
                    )
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=["get"])
    def check_payment_status(
        self, request: HttpRequest, pk: str | None = None
    ) -> Response:
        order = get_object_or_404(Order, pk=pk)

        if not self.check_order_permission(request, order):
            return Response(
                {
                    "detail": _(
                        "You do not have permission to check payment status for this order."
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        if not order.pay_way:
            return Response(
                {"detail": _("This order has no payment method selected.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not order.payment_id:
            return Response(
                {"detail": _("This order has no associated payment.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            payment_status, status_data = PayWayService.check_payment_status(
                pay_way=order.pay_way,
                order=order,
            )

            return Response(
                {
                    "order_id": order.id,
                    "payment_status": payment_status,
                    "is_paid": order.is_paid,
                    "status_details": status_data,
                }
            )

        except Exception:
            logger.exception(
                "Exception during payment status check for order %s",
                order.id,
                extra={"order_id": order.id},
            )
            return Response(
                {
                    "detail": _(
                        "An error occurred while checking payment status."
                    )
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=["post"])
    def refund_payment(
        self, request: HttpRequest, pk: str | None = None
    ) -> Response:
        order = get_object_or_404(Order, pk=pk)

        if not order.is_paid:
            return Response(
                {"detail": _("This order has not been paid.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not order.pay_way:
            return Response(
                {"detail": _("This order has no payment method selected.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        amount = None
        if "amount" in request.data and "currency" in request.data:
            from djmoney.money import Money  # noqa: PLC0415

            amount = Money(
                amount=request.data.get("amount"),
                currency=request.data.get("currency"),
            )

        try:
            success, response_data = PayWayService.refund_payment(
                pay_way=order.pay_way,
                order=order,
                amount=amount,
            )

            if not success:
                logger.error(
                    "Payment refund failed for order %s",
                    order.id,
                    extra={
                        "order_id": order.id,
                        "error": response_data.get("error", "Unknown error"),
                    },
                )
                return Response(
                    {
                        "detail": _("Payment refund failed."),
                        "error": response_data.get("error"),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            return Response(
                {
                    "detail": _("Payment refund initiated."),
                    "order_id": order.id,
                    "payment_status": order.payment_status,
                    "refund_id": response_data.get("refund_id", ""),
                    "refund_details": response_data,
                }
            )

        except Exception:
            logger.exception(
                "Exception during payment refund for order %s",
                order.id,
                extra={"order_id": order.id},
            )
            return Response(
                {"detail": _("An error occurred while processing the refund.")},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
