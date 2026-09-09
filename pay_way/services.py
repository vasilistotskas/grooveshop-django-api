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
from pay_way.enum.settlement import PaySettlement
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

        Which credentials a provider needs is the PROVIDER's knowledge,
        so it answers for itself via ``is_configured_for_tenant()``.
        This used to be an ``if code == "stripe" … elif "viva_wallet"``
        ladder here — a third place that had to list every vendor, and
        one more to find when adding a PSP.

        An unregistered code passes through as configured: ``""`` and
        offline processors (``cash``, ``bank_transfer``) need no
        credentials, and an online code with no adapter is caught later
        by the hosted-checkout capability gate rather than being
        silently hidden from the shopper here.
        """
        from order.payment import get_payment_provider_class

        provider_class = get_payment_provider_class(provider_code)
        if provider_class is None:
            return True
        return provider_class.is_configured_for_tenant()

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
    def filter_by_shipping_kind(
        queryset: QuerySet,
        *,
        shipping_kind: str | None,
    ) -> QuerySet:
        """Filter PayWays for a kind whose carrier is not yet known.

        ``home_delivery`` is provider-agnostic in checkout: the
        storefront cannot name a carrier because Django picks the
        active home-delivery provider at order-creation time
        (``carrierForMethod`` in ``shared/shipping/index.ts`` returns
        null for it by design). Such a request therefore arrives with
        ``shippingKind`` and no ``shippingProviderCode`` — and used to
        fall straight through unfiltered, so BoxNow PAY ON THE GO, a
        locker-terminal product, was selectable on courier home
        delivery. Found on staging 2026-09-09.

        Both layers are applied as an INTERSECTION over every candidate
        carrier: a pay way survives only when no candidate excludes it
        and every candidate can settle it. A union would offer a
        settlement that one candidate cannot perform — the same class
        of bug this exists to close, just narrower.

        Candidates mirror ``ShippingService.available_options``: active
        providers with a registered adapter that advertise the kind and
        do not gate it behind their own per-kind flag. When nothing
        serves the kind there is no shipping option to pair a payment
        with, so the queryset passes through unchanged — same
        short-circuit convention as :meth:`filter_by_carrier`.
        """
        if not shipping_kind:
            return queryset

        from pay_way.models import PayWayShippingExclusion
        from shipping.enum import ShippingKind
        from shipping.interfaces import get_provider, is_registered
        from shipping.models import ShippingProvider

        try:
            kind_enum = ShippingKind(shipping_kind)
        except ValueError:
            return queryset

        support_field = (
            "supports_pickup_point"
            if kind_enum == ShippingKind.PICKUP_POINT
            else "supports_home_delivery"
        )
        candidates = []
        for provider in ShippingProvider.objects.filter(
            is_active=True, **{support_field: True}
        ):
            if not is_registered(provider.code):
                continue
            adapter = get_provider(provider.code)
            if not adapter.is_kind_enabled(kind_enum):
                continue
            candidates.append((provider.code, adapter))

        if not candidates:
            return queryset

        for code, adapter in candidates:
            excluded_ids = PayWayShippingExclusion.objects.filter(
                shipping_provider__code=code,
                shipping_kind=kind_enum.value,
            ).values("pay_way_id")
            queryset = queryset.exclude(id__in=excluded_ids)
            queryset = adapter.filter_pay_ways(queryset, kind=kind_enum)

        return queryset

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
        if PaySettlement(pay_way.settlement) != PaySettlement.ONLINE:
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

            if (
                PaySettlement(pay_way.settlement)
                != PaySettlement.OFFLINE_TRANSFER
            ):
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

        if PaySettlement(pay_way.settlement) != PaySettlement.ONLINE:
            return order.payment_status, {
                "status": order.payment_status,
                "provider": "offline",
                "manual_check_required": (
                    PaySettlement(pay_way.settlement)
                    == PaySettlement.OFFLINE_TRANSFER
                ),
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

        if PaySettlement(pay_way.settlement) != PaySettlement.ONLINE:
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
