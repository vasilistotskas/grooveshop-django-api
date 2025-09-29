import logging
from abc import ABC, abstractmethod
from typing import Any

import stripe
from django.conf import settings
from djmoney.money import Money
from djstripe.models import PaymentIntent, Refund

from order.enum.status import PaymentStatus

logger = logging.getLogger(__name__)


class PaymentProvider(ABC):
    @abstractmethod
    def process_payment(
        self, amount: Money, order_id: str, **kwargs
    ) -> tuple[bool, dict[str, Any]]:
        pass

    @abstractmethod
    def refund_payment(
        self, payment_id: str, amount: Money | None = None
    ) -> tuple[bool, dict[str, Any]]:
        pass

    @abstractmethod
    def get_payment_status(
        self, payment_id: str
    ) -> tuple[PaymentStatus, dict[str, Any]]:
        pass


class StripePaymentProvider(PaymentProvider):
    def __init__(self):
        if hasattr(settings, "STRIPE_LIVE_MODE") and settings.STRIPE_LIVE_MODE:
            stripe.api_key = settings.STRIPE_LIVE_SECRET_KEY
        else:
            stripe.api_key = settings.STRIPE_TEST_SECRET_KEY

        self.webhook_secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "")

    def _map_stripe_status(self, stripe_status: str) -> PaymentStatus:
        status_mapping = {
            "requires_payment_method": PaymentStatus.PENDING,
            "requires_confirmation": PaymentStatus.PENDING,
            "requires_action": PaymentStatus.PENDING,
            "processing": PaymentStatus.PROCESSING,
            "requires_capture": PaymentStatus.PROCESSING,
            "succeeded": PaymentStatus.COMPLETED,
            "canceled": PaymentStatus.CANCELED,
        }
        return status_mapping.get(stripe_status, PaymentStatus.FAILED)

    def process_payment(
        self, amount: Money, order_id: str, **kwargs
    ) -> tuple[bool, dict[str, Any]]:
        try:
            logger.info(
                "Processing Stripe payment",
                extra={
                    "amount": str(amount.amount),
                    "currency": str(amount.currency),
                    "order_id": order_id,
                },
            )

            stripe_amount = int(amount.amount * 100)
            currency_code = str(amount.currency).lower()
            payment_intent_data = {
                "amount": stripe_amount,
                "currency": currency_code,
                "metadata": {"order_id": order_id, "source": "django_app"},
                "automatic_payment_methods": {
                    "enabled": True,
                },
            }

            customer_id = kwargs.get("customer_id")
            if customer_id:
                payment_intent_data["customer"] = customer_id

            stripe_pi = stripe.PaymentIntent.create(**payment_intent_data)
            print("Stripe PaymentIntent created successfully", stripe_pi)

            try:
                djstripe_pi = PaymentIntent.sync_from_stripe_data(stripe_pi)
                logger.info(
                    f"PaymentIntent synced to dj-stripe: {djstripe_pi.id}"
                )
            except Exception as sync_error:
                logger.warning(
                    f"Failed to sync PaymentIntent to dj-stripe: {sync_error}"
                )

            payment_data = {
                "payment_id": stripe_pi.id,
                "status": self._map_stripe_status(stripe_pi.status),
                "amount": str(amount.amount),
                "currency": str(amount.currency),
                "provider": "stripe",
                "client_secret": stripe_pi.client_secret,
                "requires_action": stripe_pi.status == "requires_action",
                "next_action": stripe_pi.next_action,
            }

            logger.info(
                "Stripe payment intent created successfully",
                extra={
                    "payment_intent_id": stripe_pi.id,
                    "status": stripe_pi.status,
                    "order_id": order_id,
                },
            )

            return True, payment_data

        except stripe.StripeError as e:
            logger.error(
                f"Stripe payment processing failed: {e}",
                extra={"order_id": order_id, "error": str(e)},
            )
            return False, {"error": str(e), "stripe_error": True}
        except Exception as e:
            logger.error(
                f"Payment processing failed: {e}",
                extra={"order_id": order_id, "error": str(e)},
            )
            return False, {"error": str(e)}

    def refund_payment(
        self, payment_id: str, amount: Money | None = None
    ) -> tuple[bool, dict[str, Any]]:
        try:
            logger.info(
                "Processing Stripe refund",
                extra={
                    "payment_id": payment_id,
                    "amount": str(amount.amount) if amount else "full",
                },
            )

            refund_params = {"payment_intent": payment_id}
            if amount:
                refund_params["amount"] = int(amount.amount * 100)
            stripe_refund = stripe.Refund.create(**refund_params)

            try:
                djstripe_refund = Refund.sync_from_stripe_data(stripe_refund)
                logger.info(f"Refund synced to dj-stripe: {djstripe_refund.id}")
            except Exception as sync_error:
                logger.warning(
                    f"Failed to sync Refund to dj-stripe: {sync_error}"
                )

            refund_data = {
                "refund_id": stripe_refund.id,
                "status": PaymentStatus.REFUNDED
                if stripe_refund.status == "succeeded"
                else PaymentStatus.PROCESSING,
                "amount": str(amount.amount) if amount else "full refund",
                "payment_id": payment_id,
                "stripe_status": stripe_refund.status,
            }

            return True, refund_data

        except stripe.StripeError as e:
            logger.error(
                f"Stripe refund failed: {e}",
                extra={"payment_id": payment_id, "error": str(e)},
            )
            return False, {"error": str(e), "stripe_error": True}
        except Exception as e:
            logger.error(
                f"Refund failed: {e}",
                extra={"payment_id": payment_id, "error": str(e)},
            )
            return False, {"error": str(e)}

    def get_payment_status(
        self, payment_id: str
    ) -> tuple[PaymentStatus, dict[str, Any]]:
        try:
            logger.info(
                "Getting Stripe payment status",
                extra={"payment_id": payment_id},
            )

            try:
                djstripe_pi = PaymentIntent.objects.get(id=payment_id)
                # Get fresh data from Stripe API
                stripe_pi = stripe.PaymentIntent.retrieve(payment_id)
                # Update local model with fresh data
                djstripe_pi = PaymentIntent.sync_from_stripe_data(stripe_pi)
            except PaymentIntent.DoesNotExist:
                # If not in local DB, retrieve from Stripe and sync
                stripe_pi = stripe.PaymentIntent.retrieve(payment_id)
                djstripe_pi = PaymentIntent.sync_from_stripe_data(stripe_pi)

            status = self._map_stripe_status(stripe_pi.status)
            status_data = {
                "payment_id": payment_id,
                "raw_status": stripe_pi.status,
                "provider": "stripe",
                "amount": stripe_pi.amount / 100,
                "currency": stripe_pi.currency.upper(),
                "created": stripe_pi.created,
                "last_updated": djstripe_pi.created if djstripe_pi else None,
            }

            return status, status_data

        except stripe.StripeError as e:
            logger.error(
                f"Failed to get Stripe payment status: {e}",
                extra={"payment_id": payment_id, "error": str(e)},
            )
            return PaymentStatus.FAILED, {"error": str(e), "stripe_error": True}
        except Exception as e:
            logger.error(
                f"Failed to get payment status: {e}",
                extra={"payment_id": payment_id, "error": str(e)},
            )
            return PaymentStatus.FAILED, {"error": str(e)}


class PayPalPaymentProvider(PaymentProvider):
    def __init__(self):
        self.client_id = getattr(settings, "PAYPAL_CLIENT_ID", "")
        self.client_secret = getattr(settings, "PAYPAL_CLIENT_SECRET", "")

    def process_payment(
        self, amount: Money, order_id: str, **kwargs
    ) -> tuple[bool, dict[str, Any]]:
        try:
            logger.info(
                "Processing PayPal payment",
                extra={
                    "amount": str(amount.amount),
                    "currency": str(amount.currency),
                    "order_id": order_id,
                },
            )

            # Mock response for demonstration
            payment_data = {
                "payment_id": f"PP_{order_id}_mock",
                "status": PaymentStatus.COMPLETED,
                "amount": str(amount.amount),
                "currency": str(amount.currency),
                "provider": "paypal",
            }

            return True, payment_data

        except Exception as e:
            logger.error(
                f"PayPal payment processing failed: {e!s}",
                extra={"order_id": order_id, "error": str(e)},
            )
            return False, {"error": str(e)}

    def refund_payment(
        self, payment_id: str, amount: Money | None = None
    ) -> tuple[bool, dict[str, Any]]:
        try:
            logger.info(
                "Processing PayPal refund",
                extra={
                    "payment_id": payment_id,
                    "amount": str(amount.amount) if amount else "full",
                },
            )

            # Mock response
            refund_data = {
                "refund_id": f"PP_RE_{payment_id}_mock",
                "status": PaymentStatus.REFUNDED,
                "amount": str(amount.amount) if amount else "full refund",
                "payment_id": payment_id,
            }

            return True, refund_data

        except Exception as e:
            logger.error(
                f"PayPal refund failed: {e!s}",
                extra={"payment_id": payment_id, "error": str(e)},
            )
            return False, {"error": str(e)}

    def get_payment_status(
        self, payment_id: str
    ) -> tuple[PaymentStatus, dict[str, Any]]:
        try:
            logger.info(
                "Getting PayPal payment status",
                extra={"payment_id": payment_id},
            )

            # Mock response
            status = PaymentStatus.COMPLETED
            status_data = {
                "payment_id": payment_id,
                "raw_status": "COMPLETED",
                "provider": "paypal",
            }

            return status, status_data

        except Exception as e:
            logger.error(
                f"Failed to get PayPal payment status: {e!s}",
                extra={"payment_id": payment_id, "error": str(e)},
            )
            return PaymentStatus.FAILED, {"error": str(e)}


def get_payment_provider(provider_name: str) -> PaymentProvider:
    providers = {
        "stripe": StripePaymentProvider,
        "paypal": PayPalPaymentProvider,
    }

    provider_class = providers.get(provider_name.lower())
    if not provider_class:
        raise ValueError(f"Unknown payment provider: {provider_name}")

    return provider_class()
