"""
Stripe webhook debugging middleware.

Logs detailed information about incoming Stripe webhook requests
to help diagnose signature verification failures.
"""

import hashlib
import logging
from typing import Callable

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

    def __call__(self, request: HttpRequest) -> HttpResponse:
        from django.conf import settings as django_settings

        debug_active = django_settings.DEBUG or getattr(
            django_settings, "STRIPE_WEBHOOK_DEBUG", False
        )
        if not debug_active:
            return self.get_response(request)

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
            body = request.body
            body_hash = hashlib.sha256(body).hexdigest()[:16]
            body_length = len(body)

            logger.info(
                "Stripe webhook request received",
                extra={
                    "path": request.path,
                    "body_length": body_length,
                    "body_hash": body_hash,
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
