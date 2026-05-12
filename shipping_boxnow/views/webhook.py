"""BoxNow webhook DRF view.

Public URL given to BoxNow for parcel-event notifications.
Authentication is done exclusively via HMAC-SHA256 datasignature
(no Knox/session auth — BoxNow is a machine-to-machine caller).

Multi-tenant: the webhook hits the platform's public schema (BoxNow
doesn't know about our tenant boundary). We resolve the owning tenant
by looking up ``data.parcelId`` across all active tenant schemas, then
verify the signature with that tenant's secret and dispatch the task
with the matching schema header. See C5 in MULTI_TENANT_AUDIT.md.
"""

from __future__ import annotations

import json
import logging

from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django_tenants.utils import get_public_schema_name, schema_context
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import extend_schema

from shipping_boxnow.serializers import (
    BoxNowWebhookEnvelopeSerializer,
    BoxNowWebhookResponseSerializer,
)
from shipping_boxnow.webhook import (
    BoxNowWebhookError,
    extract_data_substring,
    validate_envelope,
    verify_signature,
)

logger = logging.getLogger("shipping_boxnow.webhook")


def _resolve_tenant_for_parcel(parcel_id: str) -> str | None:
    """Return the schema name of the tenant that owns ``parcel_id``.

    Iterates active tenants and searches each schema's
    ``BoxNowShipment`` table for the parcel. First match wins.
    Returns ``None`` if no tenant owns the parcel (orphan webhook).
    """
    if not parcel_id:
        return None

    from shipping_boxnow.models import BoxNowShipment  # noqa: PLC0415
    from tenant.models import Tenant  # noqa: PLC0415

    public = get_public_schema_name()
    for tenant in Tenant.objects.filter(is_active=True).exclude(
        schema_name=public
    ):
        with schema_context(tenant.schema_name):
            if BoxNowShipment.objects.filter(parcel_id=parcel_id).exists():
                return tenant.schema_name
    return None


@method_decorator(csrf_exempt, name="dispatch")
class BoxNowWebhookView(APIView):
    """Receive and verify BoxNow parcel-event webhook notifications.

    Security model:
        - CSRF exempt (machine-to-machine, no browser session).
        - No DRF authentication or permission checks at the DRF layer
          (AllowAny + empty authentication_classes).
        - The only gate is the HMAC-SHA256 ``datasignature`` field, verified
          against ``settings.BOXNOW_WEBHOOK_SECRET``.

    Response contract (per BoxNow docs — they retry until 200):
        - 200: event accepted (including no-op duplicates after wave-2 service
               adds idempotency via ``webhook_message_id``).
        - 400: malformed body / invalid envelope (do not retry).
        - 401: signature invalid (stop retrying — wrong secret or tampered body).
        - 500: transient server error (BoxNow will retry).
        - 503: server misconfiguration — secret not set (BoxNow will retry).
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []
    serializer_class = BoxNowWebhookEnvelopeSerializer

    @extend_schema(
        operation_id="boxnowWebhook",
        summary="Receive a BoxNow parcel-event webhook",
        description=(
            "Public endpoint for BoxNow's machine-to-machine parcel-event"
            " notifications. Authenticated by HMAC-SHA256 datasignature"
            " verification — no Knox or session auth required. Always"
            " returns 200 once the signature is valid (even on duplicate"
            " events) to prevent BoxNow retry storms."
        ),
        tags=["BoxNow webhook"],
        request=BoxNowWebhookEnvelopeSerializer,
        responses={
            200: BoxNowWebhookResponseSerializer,
            400: None,
            401: None,
            503: None,
        },
    )
    def post(self, request: Request) -> Response:
        # ------------------------------------------------------------------ #
        # 1. Capture raw bytes before DRF does anything with the body.        #
        # ------------------------------------------------------------------ #
        raw_body: bytes = request.body

        # ------------------------------------------------------------------ #
        # 2. Parse outer envelope (JSON parse is fine here — we need the      #
        #    fields; we extract the raw data substring separately).           #
        # ------------------------------------------------------------------ #
        try:
            envelope: dict = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            logger.warning(
                "BoxNow webhook: invalid JSON body | error=%s | body_len=%d",
                exc,
                len(raw_body),
            )
            return Response(status=400)

        message_id: str = envelope.get("id", "<no-id>")

        # Log receipt at INFO — envelope minus the data payload to avoid
        # bloating logs with large bodies.  NEVER log the secret.
        logger.info(
            "BoxNow webhook received | id=%s | type=%s | subject=%s"
            " | source=%s | time=%s",
            message_id,
            envelope.get("type", ""),
            envelope.get("subject", ""),
            envelope.get("source", ""),
            envelope.get("time", ""),
        )

        try:
            # -------------------------------------------------------------- #
            # 3. Validate envelope shape (specversion, type).                 #
            # -------------------------------------------------------------- #
            validate_envelope(envelope)

            # -------------------------------------------------------------- #
            # 4. Extract raw ``data`` bytes and the datasignature.            #
            # -------------------------------------------------------------- #
            datasignature: str = envelope.get("datasignature", "")
            raw_data: bytes = extract_data_substring(raw_body)

        except BoxNowWebhookError as exc:
            logger.warning(
                "BoxNow webhook: envelope error | id=%s | error=%s",
                message_id,
                exc,
            )
            return Response(status=400)

        # ------------------------------------------------------------------ #
        # 5. Resolve the owning tenant from the parcel.                       #
        # ------------------------------------------------------------------ #
        parcel_id = (envelope.get("data") or {}).get("parcelId") or ""
        tenant_schema = _resolve_tenant_for_parcel(parcel_id)
        if not tenant_schema:
            logger.warning(
                "BoxNow webhook: cannot resolve tenant for parcelId=%s id=%s",
                parcel_id,
                message_id,
            )
            # Acknowledge with 200 so BoxNow stops retrying an orphan parcel.
            return Response(status=200)

        # ------------------------------------------------------------------ #
        # 6. Verify signature inside the tenant schema (per-tenant secret).   #
        # ------------------------------------------------------------------ #
        from tenant.credentials import box_now_credentials  # noqa: PLC0415

        with schema_context(tenant_schema):
            secret: str = box_now_credentials()["webhook_secret"]

        if not secret:
            logger.error(
                "BoxNow webhook: BOXNOW_WEBHOOK_SECRET is not configured"
                " for tenant=%s — cannot verify signature | id=%s",
                tenant_schema,
                message_id,
            )
            return Response(status=503)

        if not verify_signature(raw_data, datasignature, secret):
            logger.warning(
                "BoxNow webhook: signature mismatch | tenant=%s | id=%s"
                " | datasignature_prefix=%s",
                tenant_schema,
                message_id,
                datasignature[:8] if datasignature else "<empty>",
            )
            return Response(status=401)

        logger.info(
            "BoxNow webhook: signature verified | tenant=%s | id=%s",
            tenant_schema,
            message_id,
        )

        # ------------------------------------------------------------------ #
        # 7. Dispatch to Celery with the resolved tenant schema header.       #
        # ------------------------------------------------------------------ #
        try:
            from shipping_boxnow.tasks import process_boxnow_webhook_event

            process_boxnow_webhook_event.apply_async(
                args=[envelope],
                headers={"_schema_name": tenant_schema},
            )
        except Exception:
            logger.exception(
                "BoxNow webhook dispatch failed for message %s tenant=%s",
                envelope.get("id", "<unknown>"),
                tenant_schema,
            )
            return Response(status=500)

        return Response(status=200)
