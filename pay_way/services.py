import logging
from typing import Any

from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _
from djmoney.money import Money

from order.enum.status import (
    SETTLED_PAYMENT_STATUSES,
    PaymentStatus,
)
from order.models import Order
from order.payment import get_payment_provider
from order.signals import order_refunded
from pay_way.models import PayWay

logger = logging.getLogger(__name__)


class PayWayService:
    @staticmethod
    def is_provider_configured(provider_code: str) -> bool:
        """True when the current tenant can actually charge through the
        named provider.

        PayWay rows are per-schema, but seeding alone doesn't prove the
        tenant holds credentials — a row can be seeded before the
        merchant pastes their keys, or keys can be removed later. This
        gate keeps such providers out of the shopper-facing list AND
        blocks checkout-session creation for them.

        Only the two credentialed charge providers are gated:

        - ``stripe``: requires ``stripe_credentials()["secret_key"]`` —
          the tenant's own key. No platform-wide fallback.
        - ``viva_wallet``: requires the Smart Checkout OAuth pair
          (client id + secret).

        Every other code passes through unchanged: ``""`` and offline
        processors (``cash``, ``bank_transfer``) need no credentials,
        and unregistered/unimplemented online codes keep their existing
        behavior (listed, then rejected by the ``supported_providers``
        check at checkout-session creation).
        """
        from tenant.credentials import (  # noqa: PLC0415
            stripe_credentials,
            viva_wallet_credentials,
        )

        code = (provider_code or "").lower()
        if code == "stripe":
            return bool(stripe_credentials()["secret_key"])
        if code == "viva_wallet":
            creds = viva_wallet_credentials()
            return bool(creds["client_id"] and creds["client_secret"])
        return True

    @staticmethod
    def unconfigured_provider_codes(codes) -> set[str]:
        """Subset of ``codes`` the current tenant cannot charge through."""
        return {
            code
            for code in codes
            if code and not PayWayService.is_provider_configured(code)
        }

    @staticmethod
    def filter_by_carrier(
        queryset: QuerySet,
        *,
        provider_code: str | None,
        shipping_kind: str | None,
    ) -> QuerySet:
        """Filter PayWays compatible with the chosen carrier + kind.

        Two layers of rules apply, in order:

        1. **Admin-configured exclusions** — rows in
           ``PayWayShippingExclusion`` indexed by ``(provider, kind)``
           identify pay-ways that the operator has switched off for
           that combination from the Django admin. Soft rules,
           runtime-toggleable with no redeploy.
        2. **Carrier hard constraints** — the registered adapter's
           ``filter_pay_ways(kind)`` hook applies any code-level
           vetoes for combinations the courier API genuinely rejects
           regardless of operator preference. Default base
           implementation is pass-through.

        Args:
            queryset: Base PayWay queryset.
            provider_code: ``ShippingProvider.code`` value, or None.
            shipping_kind: ``ShippingKind`` value, or None.

        Returns:
            Filtered queryset. Empty/unknown inputs short-circuit to
            the input queryset unchanged (caller's choice to widen
            the search).
        """
        if not provider_code or not shipping_kind:
            return queryset

        from shipping.enum import ShippingKind
        from shipping.interfaces import get_provider, is_registered

        if not is_registered(provider_code):
            return queryset

        try:
            kind_enum = ShippingKind(shipping_kind)
        except ValueError:
            return queryset

        # Layer 1: admin-configured exclusions. Subquery so callers
        # composing paginated queries don't pay an extra round trip.
        from pay_way.models import PayWayShippingExclusion

        excluded_ids = PayWayShippingExclusion.objects.filter(
            shipping_provider__code=provider_code,
            shipping_kind=kind_enum.value,
        ).values("pay_way_id")
        queryset = queryset.exclude(id__in=excluded_ids)

        # Layer 2: carrier-specific hard constraints. Default
        # implementation is pass-through; specific carriers override
        # the hook for combinations the courier API itself rejects.
        adapter = get_provider(provider_code)
        return adapter.filter_pay_ways(queryset, kind=kind_enum)

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
            # Amount owed, not the raw total: a loyalty redemption is
            # deducted by calculate_order_total_amount(). Cash on
            # delivery already collected the discounted figure via
            # paid_amount, so reporting total_price here contradicted
            # the amount actually due.
            amount_due = order.calculate_order_total_amount()
            payment_data = {
                "payment_id": f"OFFLINE_{order.id}",
                "status": PaymentStatus.PENDING,
                "amount": str(amount_due.amount),
                "currency": amount_due.currency,
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
            # Amount owed, not the raw total — a loyalty redemption is
            # deducted by calculate_order_total_amount().
            amount=order.calculate_order_total_amount(),
            order_id=str(order.id),
            **kwargs,
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
            if order.payment_status in SETTLED_PAYMENT_STATUSES:
                # A settled order is final; the provider's view of the
                # payment is not the order's view of the money (a refund
                # lives on the charge, so the intent still reads
                # succeeded). Same rule as OrderService and the webhooks.
                logger.warning(
                    "Ignoring polled payment status %s for order %s: "
                    "payment_status already settled as %s",
                    status,
                    order.id,
                    order.payment_status,
                )
                return order.payment_status, status_data

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
        if order.payment_status not in (
            PaymentStatus.COMPLETED,
            PaymentStatus.PROCESSING,
        ):
            return False, {
                "error": _(
                    "Cannot refund an order that has not been paid. "
                    "Current status: %(status)s"
                )
                % {"status": order.payment_status}
            }

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

            # Flip ``payment_status`` only; ``order.status`` is a
            # business decision (the canonical transition table only
            # allows RETURNED → REFUNDED). Mirrors the policy in
            # ``handle_stripe_charge_refunded``; admin drives the
            # RETURNED→REFUNDED transition manually from the order
            # page when the goods are actually returned.
            order.payment_status = PaymentStatus.REFUNDED
            order.save(update_fields=["payment_status"])
            order_refunded.send(sender=Order, order=order)

            return True, refund_info

        provider = PayWayService.get_provider_for_pay_way(pay_way)
        if not provider:
            return False, {"error": _("Payment provider not available")}

        success, refund_data = provider.refund_payment(order.payment_id, amount)

        if success:
            order.payment_status = PaymentStatus.REFUNDED
            order.save(update_fields=["payment_status"])
            order_refunded.send(sender=Order, order=order)

        return success, refund_data
