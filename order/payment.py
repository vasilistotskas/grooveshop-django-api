import logging
from abc import ABC, abstractmethod
from typing import Any

import stripe
from django.conf import settings
from django.utils.translation import gettext_lazy as _
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

    @abstractmethod
    def create_checkout_session(
        self, amount: Money, order_id: str, **kwargs
    ) -> tuple[bool, dict[str, Any]]:
        pass


class StripePaymentProvider(PaymentProvider):
    def __init__(self):
        if hasattr(settings, "STRIPE_LIVE_MODE") and settings.STRIPE_LIVE_MODE:
            self.api_key = settings.STRIPE_LIVE_SECRET_KEY
            stripe.api_key = settings.STRIPE_LIVE_SECRET_KEY
        else:
            self.api_key = settings.STRIPE_TEST_SECRET_KEY
            stripe.api_key = settings.STRIPE_TEST_SECRET_KEY

        self.webhook_secret = getattr(settings, "DJSTRIPE_WEBHOOK_SECRET", "")

    def verify_webhook_signature(
        self, payload: bytes, signature: str
    ) -> dict[str, Any]:
        """
        Verify Stripe webhook signature.

        Uses stripe.Webhook.construct_event with DJSTRIPE_WEBHOOK_SECRET to verify
        that the webhook request actually came from Stripe and hasn't been tampered with.

        Args:
            payload: Raw webhook payload bytes from request.body
            signature: Stripe signature from HTTP_STRIPE_SIGNATURE header

        Returns:
            Verified event dictionary from Stripe

        Raises:
            stripe.error.SignatureVerificationError: If signature is invalid
            ValueError: If payload is invalid JSON

        Example:
            >>> provider = StripePaymentProvider()
            >>> event = provider.verify_webhook_signature(
            ...     payload=request.body,
            ...     signature=request.META['HTTP_STRIPE_SIGNATURE']
            ... )
            >>> print(event['type'])
            'payment_intent.succeeded'
        """
        if not self.webhook_secret:
            logger.error("DJSTRIPE_WEBHOOK_SECRET not configured")
            raise ValueError("Webhook secret not configured")

        try:
            event = stripe.Webhook.construct_event(
                payload, signature, self.webhook_secret
            )
            return event
        except ValueError as e:
            # Invalid payload
            logger.error(f"Invalid webhook payload: {e}")
            raise
        except stripe.StripeError as e:
            # Invalid signature or other Stripe error
            logger.error(f"Webhook signature verification failed: {e}")
            raise

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

    def create_checkout_session(
        self, amount: Money, order_id: str, **kwargs
    ) -> tuple[bool, dict[str, Any]]:
        from extra_settings.models import Setting

        try:
            logger.info(
                "Creating Stripe Checkout Session",
                extra={
                    "amount": str(amount.amount),
                    "currency": str(amount.currency),
                    "order_id": order_id,
                },
            )

            base_shipping_cost = Setting.get(
                "CHECKOUT_SHIPPING_PRICE", default=3.00
            )
            free_shipping_threshold = Setting.get(
                "FREE_SHIPPING_THRESHOLD", default=50.00
            )

            stripe_amount = int(amount.amount * 100)
            currency_code = str(amount.currency).lower()

            success_url = kwargs.get("success_url")
            cancel_url = kwargs.get("cancel_url")

            if not success_url or not cancel_url:
                raise ValueError("success_url and cancel_url are required")

            checkout_session_data = {
                "mode": "payment",
                "success_url": success_url,
                "cancel_url": cancel_url,
                "line_items": [
                    {
                        "price_data": {
                            "currency": currency_code,
                            "unit_amount": stripe_amount,
                            "product_data": {
                                "name": f"Order #{order_id}",
                                "description": kwargs.get(
                                    "description", "Order payment"
                                ),
                            },
                        },
                        "quantity": 1,
                    }
                ],
                "metadata": {
                    "order_id": order_id,
                    "source": "django_app",
                },
                "payment_intent_data": {
                    "metadata": {
                        "order_id": order_id,
                    }
                },
            }

            if amount.amount < free_shipping_threshold:
                checkout_session_data["shipping_options"] = [
                    {
                        "shipping_rate_data": {
                            "type": "fixed_amount",
                            "fixed_amount": {
                                "amount": int(float(base_shipping_cost) * 100),
                                "currency": currency_code,
                            },
                            "display_name": _("Standard shipping"),
                            "delivery_estimate": {
                                "minimum": {"unit": "business_day", "value": 5},
                                "maximum": {"unit": "business_day", "value": 7},
                            },
                        },
                    },
                ]

            customer_id = kwargs.get("customer_id")
            if customer_id:
                checkout_session_data["customer"] = customer_id
            else:
                checkout_session_data["customer_creation"] = "always"

            customer_email = kwargs.get("customer_email")
            if customer_email and not customer_id:
                checkout_session_data["customer_email"] = customer_email

            subscriber_id = kwargs.get("subscriber_id")
            if subscriber_id:
                subscriber_key = getattr(
                    settings,
                    "DJSTRIPE_SUBSCRIBER_CUSTOMER_KEY",
                    "djstripe_subscriber",
                )
                checkout_session_data["metadata"][subscriber_key] = str(
                    subscriber_id
                )

            session = stripe.checkout.Session.create(**checkout_session_data)

            logger.info(
                "Stripe Checkout Session created successfully",
                extra={
                    "session_id": session.id,
                    "order_id": order_id,
                },
            )

            return True, {
                "session_id": session.id,
                "checkout_url": session.url,
                "status": "created",
                "amount": str(amount.amount),
                "currency": str(amount.currency),
                "provider": "stripe",
            }

        except stripe.StripeError as e:
            logger.error(
                f"Stripe Checkout Session creation failed: {e}",
                extra={"order_id": order_id, "error": str(e)},
                exc_info=True,
            )
            return False, {"error": str(e), "stripe_error": True}
        except Exception as e:
            logger.error(
                f"Checkout Session creation failed: {e}",
                extra={"order_id": order_id, "error": str(e)},
                exc_info=True,
            )
            return False, {"error": str(e)}

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

            # Build comprehensive metadata for tracking
            metadata = {
                "order_id": order_id,
                "source": "django_app",
            }

            # Add cart_item_ids if provided
            cart_item_ids = kwargs.get("cart_item_ids")
            if cart_item_ids is not None:
                # Convert list to comma-separated string for Stripe metadata
                if isinstance(cart_item_ids, list):
                    metadata["cart_item_ids"] = ",".join(
                        str(id) for id in cart_item_ids
                    )
                else:
                    metadata["cart_item_ids"] = str(cart_item_ids)

            # Add customer_email if provided
            customer_email = kwargs.get("customer_email")
            if customer_email:
                metadata["customer_email"] = customer_email

            payment_intent_data = {
                "amount": stripe_amount,
                "currency": currency_code,
                "metadata": metadata,
                "automatic_payment_methods": {
                    "enabled": True,
                },
            }

            # Add idempotency key using order_uuid if provided
            order_uuid = kwargs.get("order_uuid")
            idempotency_key = None
            if order_uuid:
                idempotency_key = f"order_{order_uuid}"
                logger.info(
                    "Using idempotency key for payment intent",
                    extra={
                        "idempotency_key": idempotency_key,
                        "order_id": order_id,
                    },
                )

            customer_id = kwargs.get("customer_id")
            if customer_id:
                payment_intent_data["customer"] = customer_id

            # Create payment intent with idempotency key if provided
            if idempotency_key:
                stripe_pi = stripe.PaymentIntent.create(
                    **payment_intent_data, idempotency_key=idempotency_key
                )
            else:
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
                exc_info=True,
            )
            return False, {"error": str(e), "stripe_error": True}
        except Exception as e:
            logger.error(
                f"Payment processing failed: {e}",
                extra={"order_id": order_id, "error": str(e)},
                exc_info=True,
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
                exc_info=True,
            )
            return False, {"error": str(e), "stripe_error": True}
        except Exception as e:
            logger.error(
                f"Refund failed: {e}",
                extra={"payment_id": payment_id, "error": str(e)},
                exc_info=True,
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
                stripe_pi = stripe.PaymentIntent.retrieve(payment_id)
                djstripe_pi = PaymentIntent.sync_from_stripe_data(stripe_pi)
            except PaymentIntent.DoesNotExist:
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
                exc_info=True,
            )
            return PaymentStatus.FAILED, {"error": str(e), "stripe_error": True}
        except Exception as e:
            logger.error(
                f"Failed to get payment status: {e}",
                extra={"payment_id": payment_id, "error": str(e)},
                exc_info=True,
            )
            return PaymentStatus.FAILED, {"error": str(e)}


class VivaWalletPaymentProvider(PaymentProvider):
    DEMO_AUTH_URL = "https://demo-accounts.vivapayments.com/connect/token"
    LIVE_AUTH_URL = "https://accounts.vivapayments.com/connect/token"
    DEMO_API_URL = "https://demo-api.vivapayments.com"
    LIVE_API_URL = "https://api.vivapayments.com"
    DEMO_CHECKOUT_URL = "https://demo.vivapayments.com"
    LIVE_CHECKOUT_URL = "https://www.vivapayments.com"
    DEMO_TRANSACTIONS_URL = "https://demo.vivapayments.com"
    LIVE_TRANSACTIONS_URL = "https://www.vivapayments.com"
    TOKEN_CACHE_KEY = "viva_wallet_access_token"

    def __init__(self):
        self.merchant_id = getattr(settings, "VIVA_WALLET_MERCHANT_ID", "")
        self.api_key = getattr(settings, "VIVA_WALLET_API_KEY", "")
        self.client_id = getattr(settings, "VIVA_WALLET_CLIENT_ID", "")
        self.client_secret = getattr(settings, "VIVA_WALLET_CLIENT_SECRET", "")
        self.source_code = getattr(
            settings, "VIVA_WALLET_SOURCE_CODE", "Default"
        )
        self.live_mode = getattr(settings, "VIVA_WALLET_LIVE_MODE", False)

        if self.live_mode:
            self.auth_url = self.LIVE_AUTH_URL
            self.api_url = self.LIVE_API_URL
            self.checkout_url = self.LIVE_CHECKOUT_URL
            self.transactions_url = self.LIVE_TRANSACTIONS_URL
        else:
            self.auth_url = self.DEMO_AUTH_URL
            self.api_url = self.DEMO_API_URL
            self.checkout_url = self.DEMO_CHECKOUT_URL
            self.transactions_url = self.DEMO_TRANSACTIONS_URL

    def _get_access_token(self) -> str:
        from base64 import b64encode

        import requests
        from django.core.cache import cache

        cached_token = cache.get(self.TOKEN_CACHE_KEY)
        if cached_token:
            return cached_token

        credentials = b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()

        response = requests.post(
            self.auth_url,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "client_credentials",
            },
            timeout=30,
        )
        response.raise_for_status()
        token_data = response.json()

        access_token = token_data["access_token"]
        expires_in = token_data.get("expires_in", 3600)
        cache.set(
            self.TOKEN_CACHE_KEY,
            access_token,
            timeout=max(expires_in - 60, 60),
        )

        return access_token

    def _get_basic_auth_header(self) -> str:
        from base64 import b64encode

        credentials = b64encode(
            f"{self.merchant_id}:{self.api_key}".encode()
        ).decode()
        return f"Basic {credentials}"

    def _map_viva_status(self, status_id: str) -> PaymentStatus:
        status_mapping = {
            "F": PaymentStatus.COMPLETED,
            "A": PaymentStatus.PENDING,
            "C": PaymentStatus.COMPLETED,
            "E": PaymentStatus.FAILED,
            "R": PaymentStatus.REFUNDED,
            "X": PaymentStatus.CANCELED,
            "M": PaymentStatus.PROCESSING,
            "MA": PaymentStatus.PROCESSING,
            "MI": PaymentStatus.PROCESSING,
            "ML": PaymentStatus.REFUNDED,
            "MW": PaymentStatus.COMPLETED,
            "MS": PaymentStatus.PROCESSING,
        }
        return status_mapping.get(status_id, PaymentStatus.FAILED)

    def create_checkout_session(
        self, amount: Money, order_id: str, **kwargs
    ) -> tuple[bool, dict[str, Any]]:
        import requests

        try:
            logger.info(
                "Creating Viva Wallet payment order",
                extra={
                    "amount": str(amount.amount),
                    "currency": str(amount.currency),
                    "order_id": order_id,
                },
            )

            access_token = self._get_access_token()
            viva_amount = int(amount.amount * 100)

            payload = {
                "amount": viva_amount,
                "customerTrns": kwargs.get("description", f"Order #{order_id}"),
                "merchantTrns": str(order_id),
                "sourceCode": self.source_code,
                "currencyCode": 978,
                "disableCash": True,
            }

            customer_email = kwargs.get("customer_email")
            if customer_email:
                payload["customer"] = {"email": customer_email}

            response = requests.post(
                f"{self.api_url}/checkout/v2/orders",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()

            order_code = data["orderCode"]
            checkout_url = f"{self.checkout_url}/web/checkout?ref={order_code}"

            logger.info(
                "Viva Wallet payment order created",
                extra={
                    "order_code": order_code,
                    "order_id": order_id,
                },
            )

            return True, {
                "session_id": str(order_code),
                "checkout_url": checkout_url,
                "status": "created",
                "amount": str(amount.amount),
                "currency": str(amount.currency),
                "provider": "viva_wallet",
                "viva_order_code": order_code,
            }

        except requests.HTTPError as e:
            error_body = ""
            if e.response is not None:
                try:
                    error_body = e.response.json()
                except Exception:
                    error_body = e.response.text
            logger.error(
                "Viva Wallet payment order creation failed: %s %s",
                e,
                error_body,
                extra={"order_id": order_id},
                exc_info=True,
            )
            return False, {
                "error": str(e),
                "viva_error": True,
                "details": error_body,
            }
        except Exception as e:
            logger.error(
                "Viva Wallet payment order creation failed: %s",
                e,
                extra={"order_id": order_id},
                exc_info=True,
            )
            return False, {"error": str(e)}

    def process_payment(
        self, amount: Money, order_id: str, **kwargs
    ) -> tuple[bool, dict[str, Any]]:
        return self.create_checkout_session(amount, order_id, **kwargs)

    def refund_payment(
        self, payment_id: str, amount: Money | None = None
    ) -> tuple[bool, dict[str, Any]]:
        import requests

        try:
            logger.info(
                "Processing Viva Wallet refund",
                extra={
                    "payment_id": payment_id,
                    "amount": str(amount.amount) if amount else "full",
                },
            )

            params = {}
            if amount:
                params["amount"] = int(amount.amount * 100)

            response = requests.delete(
                f"{self.transactions_url}/api/transactions/{payment_id}",
                headers={
                    "Authorization": self._get_basic_auth_header(),
                },
                params=params,
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()

            refund_data = {
                "refund_id": data.get("TransactionId", ""),
                "status": PaymentStatus.REFUNDED,
                "amount": str(amount.amount) if amount else "full refund",
                "payment_id": payment_id,
                "viva_status": "refunded",
            }

            return True, refund_data

        except requests.HTTPError as e:
            error_body = ""
            if e.response is not None:
                try:
                    error_body = e.response.json()
                except Exception:
                    error_body = e.response.text
            logger.error(
                "Viva Wallet refund failed: %s %s",
                e,
                error_body,
                extra={"payment_id": payment_id},
                exc_info=True,
            )
            return False, {
                "error": str(e),
                "viva_error": True,
                "details": error_body,
            }
        except Exception as e:
            logger.error(
                "Viva Wallet refund failed: %s",
                e,
                extra={"payment_id": payment_id},
                exc_info=True,
            )
            return False, {"error": str(e)}

    def get_payment_status(
        self, payment_id: str
    ) -> tuple[PaymentStatus, dict[str, Any]]:
        import requests

        try:
            logger.info(
                "Getting Viva Wallet payment status",
                extra={"payment_id": payment_id},
            )

            access_token = self._get_access_token()

            response = requests.get(
                f"{self.api_url}/checkout/v2/transactions/{payment_id}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                },
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()

            status_id = data.get("statusId", "")
            status = self._map_viva_status(status_id)

            status_data = {
                "payment_id": payment_id,
                "raw_status": status_id,
                "provider": "viva_wallet",
                "amount": data.get("amount"),
                "currency": data.get("currencyCode", "EUR"),
                "order_code": data.get("orderCode"),
                "card_number": data.get("cardNumber"),
                "created": data.get("insDate"),
            }

            return status, status_data

        except requests.HTTPError as e:
            error_body = ""
            if e.response is not None:
                try:
                    error_body = e.response.json()
                except Exception:
                    error_body = e.response.text
            logger.error(
                "Failed to get Viva Wallet payment status: %s %s",
                e,
                error_body,
                extra={"payment_id": payment_id},
                exc_info=True,
            )
            return PaymentStatus.FAILED, {
                "error": str(e),
                "viva_error": True,
                "details": error_body,
            }
        except Exception as e:
            logger.error(
                "Failed to get Viva Wallet payment status: %s",
                e,
                extra={"payment_id": payment_id},
                exc_info=True,
            )
            return PaymentStatus.FAILED, {"error": str(e)}


class PayPalPaymentProvider(PaymentProvider):
    def __init__(self):
        self.client_id = getattr(settings, "PAYPAL_CLIENT_ID", "")
        self.client_secret = getattr(settings, "PAYPAL_CLIENT_SECRET", "")

    def create_checkout_session(
        self, amount: Money, order_id: str, **kwargs
    ) -> tuple[bool, dict[str, Any]]:
        return False, {"error": "Checkout sessions not supported for PayPal"}

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
                exc_info=True,
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
                exc_info=True,
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
                exc_info=True,
            )
            return PaymentStatus.FAILED, {"error": str(e)}


def get_payment_provider(provider_name: str) -> PaymentProvider:
    providers = {
        "stripe": StripePaymentProvider,
        "paypal": PayPalPaymentProvider,
        "viva_wallet": VivaWalletPaymentProvider,
    }

    provider_class = providers.get(provider_name.lower())
    if not provider_class:
        raise ValueError(f"Unknown payment provider: {provider_name}")

    return provider_class()
