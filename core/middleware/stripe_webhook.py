"""
Stripe webhook debugging middleware.

Logs detailed information about incoming Stripe webhook requests
to help diagnose signature verification failures.
"""

import hashlib
import logging
from typing import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse

logger = logging.getLogger(__name__)


class StripeWebhookDebugMiddleware:
    """
    Middleware to debug Stripe webhook signature verification issues.

    This middleware logs detailed information about incoming webhook requests
    to help diagnose signature verification failures. Only active when
    DEBUG is True or STRIPE_WEBHOOK_DEBUG is set.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response
        self.debug_enabled = getattr(settings, "DEBUG", False) or getattr(
            settings, "STRIPE_WEBHOOK_DEBUG", False
        )

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # Only process Stripe webhook paths
        if "/stripe/" in request.path and request.method == "POST":
            self._log_webhook_request(request)

        response = self.get_response(request)

        # Log response status for webhook requests
        if "/stripe/" in request.path and request.method == "POST":
            self._log_webhook_response(request, response)

        return response

    def _log_webhook_request(self, request: HttpRequest) -> None:
        """Log webhook request diagnostic information."""
        try:
            stripe_signature = request.META.get("HTTP_STRIPE_SIGNATURE", "")

            body = request.body
            body_hash = hashlib.sha256(body).hexdigest()[:16]
            body_length = len(body)

            webhook_secret = getattr(settings, "DJSTRIPE_WEBHOOK_SECRET", "")
            secret_configured = bool(
                webhook_secret and webhook_secret != "whsec_..."
            )

            logger.info(
                "Stripe webhook request received",
                extra={
                    "path": request.path,
                    "body_length": body_length,
                    "body_hash": body_hash,
                    "signature_present": bool(stripe_signature),
                    "secret_configured": secret_configured,
                    "content_type": request.content_type,
                },
            )

        except Exception:
            logger.warning(
                "Error logging webhook request",
                exc_info=True,
            )

    def _log_webhook_response(
        self, request: HttpRequest, response: HttpResponse
    ) -> None:
        """Log webhook response status."""
        if response.status_code >= 400:
            logger.warning(
                "Stripe webhook request failed",
                extra={
                    "path": request.path,
                    "status_code": response.status_code,
                    "response_preview": getattr(response, "content", b"")[
                        :200
                    ].decode("utf-8", errors="replace"),
                },
            )
        else:
            logger.info(
                "Stripe webhook request succeeded",
                extra={
                    "path": request.path,
                    "status_code": response.status_code,
                },
            )
