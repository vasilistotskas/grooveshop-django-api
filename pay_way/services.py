import logging
from typing import Any

from django.utils.translation import gettext_lazy as _
from djmoney.money import Money

from order.enum.status import OrderStatus, PaymentStatus
from order.models import Order
from order.payment import get_payment_provider
from pay_way.models import PayWay

logger = logging.getLogger(__name__)


class PayWayService:
    @staticmethod
    def get_provider_for_pay_way(pay_way: PayWay):
        if not pay_way.provider_code:
            logger.warning(f"PayWay {pay_way.id} has no provider_code defined")
            return None

        try:
            return get_payment_provider(pay_way.provider_code)
        except ValueError as e:
            logger.error(f"Failed to get payment provider: {e}")
            return None

    @staticmethod
    def process_payment(
        pay_way: PayWay, order: Order, **kwargs
    ) -> tuple[bool, dict[str, Any]]:
        if not pay_way.is_online_payment:
            payment_data = {
                "payment_id": f"OFFLINE_{order.id}",
                "status": PaymentStatus.PENDING,
                "amount": str(order.total_price.amount),
                "currency": order.total_price.currency,
                "provider": pay_way.provider_code or "offline",
            }

            if not pay_way.requires_confirmation:
                order.payment_method = (
                    pay_way.safe_translation_getter("name", any_language=True)
                    or ""
                )
                order.payment_status = PaymentStatus.PENDING
                order.payment_id = payment_data["payment_id"]
                order.save(
                    update_fields=[
                        "payment_method",
                        "payment_status",
                        "payment_id",
                    ]
                )

            return True, payment_data

        provider = PayWayService.get_provider_for_pay_way(pay_way)
        if not provider:
            return False, {"error": _("Payment provider not available")}

        success, payment_data = provider.process_payment(
            amount=order.total_price, order_id=str(order.id), **kwargs
        )

        if success:
            order.payment_method = (
                pay_way.safe_translation_getter("name", any_language=True) or ""
            )
            order.payment_status = payment_data.get(
                "status", PaymentStatus.PROCESSING
            )
            order.payment_id = payment_data.get("payment_id", "")
            order.save(
                update_fields=["payment_method", "payment_status", "payment_id"]
            )

            if order.payment_status == PaymentStatus.COMPLETED:
                order.mark_as_paid(
                    payment_id=payment_data.get("payment_id", ""),
                    payment_method=order.payment_method,
                )

        return success, payment_data

    @staticmethod
    def check_payment_status(
        pay_way: PayWay, order: Order
    ) -> tuple[PaymentStatus, dict[str, Any]]:
        if not order.payment_id:
            return PaymentStatus.PENDING, {
                "error": _("No payment ID found for order")
            }

        if not pay_way.is_online_payment:
            return order.payment_status, {
                "status": order.payment_status,
                "provider": "offline",
                "manual_check_required": pay_way.requires_confirmation,
            }

        provider = PayWayService.get_provider_for_pay_way(pay_way)
        if not provider:
            return PaymentStatus.PENDING, {
                "error": _("Payment provider not available")
            }

        status, status_data = provider.get_payment_status(order.payment_id)

        if status != order.payment_status:
            order.payment_status = status
            order.save(update_fields=["payment_status"])

            if status == PaymentStatus.COMPLETED:
                order.mark_as_paid(
                    payment_id=order.payment_id,
                    payment_method=order.payment_method,
                )

        return status, status_data

    @staticmethod
    def refund_payment(
        pay_way: PayWay, order: Order, amount: Money | None = None
    ) -> tuple[bool, dict[str, Any]]:
        if not order.payment_id:
            return False, {"error": _("No payment ID found for order")}

        if not pay_way.is_online_payment:
            if amount and amount.amount > 0:
                refund_info = {
                    "refund_id": f"MANUAL_REFUND_{order.id}",
                    "status": PaymentStatus.PENDING,
                    "amount": str(amount.amount),
                    "currency": amount.currency,
                    "provider": "manual",
                    "note": _("Manual refund process required"),
                }
            else:
                refund_info = {
                    "refund_id": f"MANUAL_REFUND_{order.id}",
                    "status": PaymentStatus.PENDING,
                    "amount": "full refund",
                    "provider": "manual",
                    "note": _("Manual refund process required"),
                }

            order.payment_status = PaymentStatus.REFUNDED
            order.status = OrderStatus.REFUNDED
            order.save(update_fields=["payment_status", "status"])

            return True, refund_info

        provider = PayWayService.get_provider_for_pay_way(pay_way)
        if not provider:
            return False, {"error": _("Payment provider not available")}

        success, refund_data = provider.refund_payment(order.payment_id, amount)

        if success:
            order.payment_status = PaymentStatus.REFUNDED
            order.status = OrderStatus.REFUNDED
            order.save(update_fields=["payment_status", "status"])

        return success, refund_data
