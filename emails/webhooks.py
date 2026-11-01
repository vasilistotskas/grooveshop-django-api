"""The Resend webhook receiver: ``POST /resend/webhook/``.

Mounted on every host, because each Resend team points its endpoint at the
host that owns its keys:

* a store with its own team registers ``https://<its api host>/resend/webhook/``
  and the delivery is verified with that store's own
  ``Tenant.resend_webhook_secret``, resolved by the host it arrived on;
* the platform's team registers the platform host, verified with
  ``settings.RESEND_WEBHOOK_SECRET``, and its events are routed by their
  ``schema`` tag to the store (or ``public``) that sent the mail.

There is no fallback between the two secrets. The body is verified byte for
byte before it is parsed, the work is a Celery task, and the answer is 200 at
once, so a slow database never makes Resend retry a delivery it already
made. Nothing from an unverified request is logged.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping

from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from standardwebhooks.exceptions import WebhookVerificationError
from standardwebhooks.webhooks import EmptyWebhookSecretError, Webhook

from emails.tasks import process_provider_event
from tenant.credentials import in_tenant_schema, tenant_resend_webhook_secret

logger = logging.getLogger(__name__)


#: Resend signs with Svix, whose three headers are the Standard Webhooks
#: ones under an older prefix; the library reads the ``webhook-`` names.
_STANDARD_HEADERS = {
    "svix-id": "webhook-id",
    "svix-timestamp": "webhook-timestamp",
    "svix-signature": "webhook-signature",
}


class InvalidSignature(Exception):
    """The request is not a fresh, correctly signed delivery."""


def verify_signature(
    secret: str, headers: Mapping[str, str], body: bytes
) -> str:
    """Return the verified ``svix-id``; raise :class:`InvalidSignature`.

    Verification is the Standard Webhooks library's (HMAC-SHA256 over
    ``id.timestamp.body``, any ``v1`` signature, a five-minute timestamp
    tolerance as the replay guard). *body* is the raw request body, byte for
    byte. Everything that library can raise for a request that is not a
    delivery (a missing header, a stale timestamp, a malformed signature or
    secret, a body that is not UTF-8) is one :class:`InvalidSignature`, so a
    forged request is a 400, never a 500.
    """
    standard = {
        new: headers.get(old, "") for old, new in _STANDARD_HEADERS.items()
    }
    try:
        Webhook(secret).verify(body, standard, json_parse=False)
    except WebhookVerificationError, EmptyWebhookSecretError, ValueError:
        raise InvalidSignature from None
    return standard["webhook-id"]


def webhook_secret() -> str:
    """The secret of the host the request arrived on: the store's own on a
    store's host, the platform's on the platform host. ``""`` when that host
    has none."""
    if in_tenant_schema():
        return tenant_resend_webhook_secret()
    return settings.RESEND_WEBHOOK_SECRET


@method_decorator(csrf_exempt, name="dispatch")
class ResendWebhookView(APIView):
    """Verify a Resend delivery and queue its event.

    * 200: verified and queued (also for an event type nothing acts on).
    * 400: not a fresh, correctly signed delivery, or not a JSON object.
    * 413: the body is larger than ``RESEND_WEBHOOK_MAX_BODY_BYTES``.
    * 503: this host has no webhook secret, so it cannot tell a real
      delivery from a forged one; Resend retries until it is set.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    # A machine-to-machine receiver, not part of the contract the storefront
    # generates its types from.
    @extend_schema(exclude=True)
    def post(self, request: Request) -> Response:
        body: bytes = request.body
        if len(body) > settings.RESEND_WEBHOOK_MAX_BODY_BYTES:
            return Response(status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        secret = webhook_secret()
        if not secret:
            logger.warning("Resend webhook refused: this host has no secret")
            return Response(status=status.HTTP_503_SERVICE_UNAVAILABLE)
        try:
            svix_id = verify_signature(secret, request.headers, body)
        except InvalidSignature:
            logger.warning("Resend webhook refused: invalid signature")
            return Response(status=status.HTTP_400_BAD_REQUEST)
        try:
            payload = json.loads(body.decode("utf-8"))
        except ValueError, RecursionError:
            return Response(status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(payload, dict):
            return Response(status=status.HTTP_400_BAD_REQUEST)
        process_provider_event.delay(svix_id, payload)
        return Response(status=status.HTTP_200_OK)
