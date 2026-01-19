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
        """Log detailed webhook request information."""
        try:
            # Get the Stripe signature header
            stripe_signature = request.META.get("HTTP_STRIPE_SIGNATURE", "")

            # Get body hash (don't log actual body for security)
            body = request.body
            body_hash = hashlib.sha256(body).hexdigest()[:16]
            body_length = len(body)

            # Parse signature components
            sig_parts = {}
            if stripe_signature:
                for part in stripe_signature.split(","):
                    if "=" in part:
                        key, value = part.split("=", 1)
                        sig_parts[key] = value[:20] + "..."  # Truncate for logging

            # Get webhook secret info (just existence and prefix, not full secret)
            webhook_secret = getattr(settings, "DJSTRIPE_WEBHOOK_SECRET", "")
            secret_configured = bool(webhook_secret and webhook_secret != "whsec_...")
            secret_prefix = (
                webhook_secret[:10] + "..." if len(webhook_secret) > 10 else "***"
            )

            # Log all the diagnostic info
            logger.info(
                "Stripe webhook request received",
                extra={
                    "path": request.path,
                    "body_length": body_length,
                    "body_hash": body_hash,
                    "signature_present": bool(stripe_signature),
                    "signature_parts": sig_parts,
                    "secret_configured": secret_configured,
                    "secret_prefix": secret_prefix if self.debug_enabled else "***",
                    "content_type": request.content_type,
                    "remote_addr": request.META.get("REMOTE_ADDR"),
                    "x_forwarded_for": request.META.get("HTTP_X_FORWARDED_FOR"),
                    "host": request.META.get("HTTP_HOST"),
                },
            )

            # Additional debug logging when enabled
            if self.debug_enabled:
                logger.debug(
                    "Stripe webhook debug info",
                    extra={
                        "full_signature": stripe_signature[:50] + "..."
                        if stripe_signature
                        else "MISSING",
                        "body_preview": body[:100].decode("utf-8", errors="replace")
                        if body
                        else "EMPTY",
                    },
                )

        except Exception as e:
            logger.warning(
                f"Error logging webhook request: {e}",
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
                    "response_preview": getattr(response, "content", b"")[:200].decode(
                        "utf-8", errors="replace"
                    ),
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
