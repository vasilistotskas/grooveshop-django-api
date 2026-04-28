import ipaddress
import json
import logging
from base64 import b64encode

import requests
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from order.enum.status import OrderStatus, PaymentStatus
from order.models.history import OrderHistory
from order.models.order import Order
from order.tasks import (
    send_order_confirmation_email,
    send_payment_failed_email,
)

logger = logging.getLogger(__name__)

# Viva Wallet production webhook source IPs (from official docs).
# https://developer.viva.com/webhooks-for-payments/
VIVA_WEBHOOK_IPS_PRODUCTION = [
    ipaddress.ip_network("51.138.37.238/32"),
    ipaddress.ip_network("13.80.70.181/32"),
    ipaddress.ip_network("13.80.71.223/32"),
    ipaddress.ip_network("13.79.28.70/32"),
    ipaddress.ip_network("40.127.253.112/28"),
    ipaddress.ip_network("51.105.129.192/28"),
    ipaddress.ip_network("20.54.89.16/32"),
    ipaddress.ip_network("4.223.76.50/32"),
    ipaddress.ip_network("51.12.157.0/28"),
]

VIVA_WEBHOOK_IPS_DEMO = [
    ipaddress.ip_network("20.50.240.57/32"),
    ipaddress.ip_network("40.74.20.78/32"),
    ipaddress.ip_network("94.70.170.65/32"),
    ipaddress.ip_network("94.70.255.73/32"),
    ipaddress.ip_network("94.70.248.18/32"),
    ipaddress.ip_network("83.235.24.226/32"),
    ipaddress.ip_network("20.13.195.185/32"),
    ipaddress.ip_network("94.70.174.36/32"),
]


@require_http_methods(["GET"])
def resolve_viva_order_code(request):
    """Resolve a Viva Wallet order code to an order UUID.

    Used by the frontend to redirect from Viva's payment page
    to the order success page.
    """
    order_code = request.GET.get("order_code", "")
    if not order_code:
        return JsonResponse({"error": "order_code required"}, status=400)

    order = (
        Order.objects.filter(metadata__viva_order_code=str(order_code))
        .values("uuid")
        .first()
    )

    if not order:
        return JsonResponse({"error": "not found"}, status=404)

    return JsonResponse({"uuid": str(order["uuid"])})


@csrf_exempt
@require_http_methods(["GET", "POST"])
def viva_wallet_webhook(request):
    if request.method == "GET":
        return _handle_verification(request)
    return _handle_webhook_event(request)


def _handle_verification(request):
    logger.info(
        "Viva webhook GET verification request | "
        "remote_addr=%s | x-forwarded-for=%s",
        request.META.get("REMOTE_ADDR", ""),
        request.META.get("HTTP_X_FORWARDED_FOR", ""),
    )
    verification_key = getattr(
        settings, "VIVA_WALLET_WEBHOOK_VERIFICATION_KEY", ""
    )

    if verification_key:
        logger.info("Using configured VIVA_WALLET_WEBHOOK_VERIFICATION_KEY")
    else:
        logger.info(
            "VIVA_WALLET_WEBHOOK_VERIFICATION_KEY not set — fetching from Viva"
        )
        verification_key = _fetch_verification_key()

    if not verification_key:
        logger.error(
            "Viva Wallet webhook verification key unavailable — "
            "GET verification will fail"
        )
        return JsonResponse({"error": "Not configured"}, status=500)

    logger.info(
        "Returning Viva verification key (first 8 chars: %s...)",
        verification_key[:8],
    )
    return JsonResponse(
        {"Key": verification_key},
        json_dumps_params={"separators": (",", ":")},
    )


def _fetch_verification_key():
    merchant_id = getattr(settings, "VIVA_WALLET_MERCHANT_ID", "")
    api_key = getattr(settings, "VIVA_WALLET_API_KEY", "")

    if not merchant_id or not api_key:
        logger.error(
            "VIVA_WALLET_MERCHANT_ID or VIVA_WALLET_API_KEY not configured"
        )
        return ""

    live_mode = getattr(settings, "VIVA_WALLET_LIVE_MODE", False)
    base_url = (
        "https://www.vivapayments.com"
        if live_mode
        else "https://demo.vivapayments.com"
    )

    try:
        credentials = b64encode(f"{merchant_id}:{api_key}".encode()).decode()
        response = requests.get(
            f"{base_url}/api/messages/config/token",
            headers={"Authorization": f"Basic {credentials}"},
            timeout=10,
        )
        response.raise_for_status()
        return response.json().get("Key", "")
    except Exception:
        logger.exception("Failed to fetch Viva Wallet verification key")
        return ""


def _check_source_ip(request) -> tuple[bool, str]:
    """Best-effort check of the webhook source IP.

    Returns (is_viva_ip, observed_ip). Used as a non-blocking signal:
    when the IP IS in Viva's range we can skip the Retrieve Transaction
    API call as an optimization. When it ISN'T we MUST fall back to the
    API call to authenticate the webhook.

    Why this isn't a hard gate: in Kubernetes with Traefik and
    `externalTrafficPolicy: Cluster` the source IP is SNAT-ed to a node
    or pod IP (e.g. 10.42.x.x) — so the original Viva IP is lost both
    in REMOTE_ADDR and in X-Forwarded-For. Hard-rejecting on IP would
    block every real webhook. The Retrieve Transaction API call is the
    real authentication: it requires our own OAuth2 credentials and
    confirms the transaction exists in Viva's system.
    """
    live_mode = getattr(settings, "VIVA_WALLET_LIVE_MODE", False)
    allowed_networks = (
        VIVA_WEBHOOK_IPS_PRODUCTION if live_mode else VIVA_WEBHOOK_IPS_DEMO
    )

    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        # Try every entry — the original Viva IP may be anywhere in the chain
        # depending on how many proxies SNAT-ed the request.
        candidates = [ip.strip() for ip in forwarded_for.split(",")]
    else:
        candidates = [
            request.META.get(
                "HTTP_X_REAL_IP",
                request.META.get("REMOTE_ADDR", ""),
            )
        ]

    observed = candidates[0] if candidates else ""

    for ip_str in candidates:
        if not ip_str:
            continue
        try:
            client_ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        for network in allowed_networks:
            if client_ip in network:
                return True, ip_str

    return False, observed


def _verify_transaction(transaction_id):
    from order.payment import VivaWalletPaymentProvider

    try:
        provider = VivaWalletPaymentProvider()
        logger.info(
            "_verify_transaction: calling provider.get_payment_status(%s) "
            "live_mode=%s api_url=%s",
            transaction_id,
            provider.live_mode,
            provider.api_url,
        )
        status, data = provider.get_payment_status(transaction_id)
        logger.info(
            "_verify_transaction: success | transaction_id=%s | "
            "status=%s | raw_status=%s",
            transaction_id,
            status,
            data.get("raw_status") if isinstance(data, dict) else None,
        )
        return status, data
    except Exception as exc:
        logger.exception(
            "_verify_transaction: FAILED for %s | error=%s",
            transaction_id,
            exc,
        )
        return None, {}


def _handle_webhook_event(request):
    from django.db import transaction

    # === DEBUG: log all request details ===
    logger.info(
        "Viva webhook POST received | "
        "remote_addr=%s | x-forwarded-for=%s | x-real-ip=%s | "
        "content_type=%s | content_length=%s | host=%s",
        request.META.get("REMOTE_ADDR", ""),
        request.META.get("HTTP_X_FORWARDED_FOR", ""),
        request.META.get("HTTP_X_REAL_IP", ""),
        request.META.get("CONTENT_TYPE", ""),
        request.META.get("CONTENT_LENGTH", ""),
        request.META.get("HTTP_HOST", ""),
    )

    # Best-effort IP check — informational only. Authentication of the
    # webhook is done by the Retrieve Transaction API call inside
    # _handle_payment_created, which uses our OAuth2 credentials and
    # confirms the transaction exists in Viva's system.
    ip_match, observed_ip = _check_source_ip(request)
    if ip_match:
        logger.info("Viva webhook IP %s matches Viva range", observed_ip)
    else:
        logger.info(
            "Viva webhook from non-Viva IP %s — will rely on transaction API "
            "verification (expected behind SNAT'd ingress)",
            observed_ip,
        )

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error(
            "Invalid JSON in Viva Wallet webhook | error=%s | body_len=%d | "
            "body_preview=%s",
            exc,
            len(request.body),
            request.body[:500].decode("utf-8", errors="replace"),
        )
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    logger.info("Viva webhook full payload: %s", json.dumps(body, default=str))

    event_data = body.get("EventData", {})
    event_type_id = body.get("EventTypeId")

    transaction_id = event_data.get("TransactionId", "")
    order_code = event_data.get("OrderCode")
    status_id = event_data.get("StatusId", "")

    logger.info(
        "Viva Wallet webhook parsed",
        extra={
            "event_type_id": event_type_id,
            "transaction_id": transaction_id,
            "order_code": order_code,
            "status_id": status_id,
        },
    )

    if not order_code:
        logger.warning("No OrderCode in Viva Wallet webhook")
        return JsonResponse({"status": "ok"})

    order = Order.objects.filter(
        metadata__viva_order_code=str(order_code)
    ).first()

    if not order:
        logger.error(
            "Order not found for Viva Wallet order code: %s | "
            "(was searching for metadata.viva_order_code = '%s')",
            order_code,
            str(order_code),
        )
        return JsonResponse({"status": "ok"})

    logger.info(
        "Viva webhook matched order #%s (uuid=%s, status=%s, payment_status=%s)",
        order.id,
        order.uuid,
        order.status,
        order.payment_status,
    )

    event_key = f"viva_webhook_{transaction_id}_{event_type_id}"
    if order.metadata and order.metadata.get(event_key):
        logger.info(
            "Viva Wallet webhook already processed: %s (idempotency hit)",
            event_key,
        )
        return JsonResponse({"status": "ok"})

    try:
        with transaction.atomic():
            # Re-fetch with row lock to prevent race conditions
            order = Order.objects.select_for_update().get(pk=order.pk)

            # Double-check idempotency after acquiring lock
            if order.metadata and order.metadata.get(event_key):
                logger.info(
                    "Viva webhook %s processed by parallel request — skipping",
                    event_key,
                )
                return JsonResponse({"status": "ok"})

            if not order.metadata:
                order.metadata = {}
            order.metadata[event_key] = True

            # Event type IDs per Viva documentation:
            # 1796 = Transaction Payment Created
            # 1797 = Transaction Reversal Created
            # 1798 = Transaction Failed
            logger.info(
                "Viva webhook dispatching event_type=%s for order #%s",
                event_type_id,
                order.id,
            )
            if event_type_id == 1796:
                _handle_payment_created(order, event_data, transaction_id)
            elif event_type_id == 1797:
                _handle_reversal_created(order, event_data, transaction_id)
            elif event_type_id == 1798:
                _handle_payment_failed(order, event_data, transaction_id)
            else:
                logger.info(
                    "Unhandled Viva Wallet event type: %s",
                    event_type_id,
                )
                order.save(update_fields=["metadata"])
    except RuntimeError as exc:
        # Raised by _handle_payment_created when Viva's verification API
        # is unreachable. Returning 500 signals Viva to retry the webhook;
        # the event_key is NOT persisted (transaction rolled back) so the
        # retry will be processed fresh.
        logger.error("Viva webhook processing error: %s", exc)
        return JsonResponse(
            {"error": "Internal verification error, please retry"},
            status=500,
        )

    return JsonResponse({"status": "ok"})


def _handle_payment_created(order, event_data, transaction_id):
    from django.utils import timezone

    logger.info(
        "_handle_payment_created START | order=%s | transaction_id=%s | "
        "current_payment_status=%s | current_status=%s",
        order.id,
        transaction_id,
        order.payment_status,
        order.status,
    )

    # Per Viva docs: check StatusId from the webhook payload first.
    # "F" = Finished (successful). Any other value means the payment
    # is not yet complete.
    status_id = event_data.get("StatusId", "")
    logger.info(
        "Viva webhook StatusId for order %s: '%s'",
        order.id,
        status_id,
    )
    if status_id and status_id != "F":
        logger.warning(
            "Viva webhook StatusId is '%s' (not 'F') for order %s — "
            "skipping payment update",
            status_id,
            order.id,
        )
        order.save(update_fields=["metadata"])
        return

    # Per Viva docs: verify via Retrieve Transaction API as extra
    # confirmation. Do NOT trust the webhook payload alone.
    # A payment-created event without a TransactionId is not verifiable
    # and must be rejected to prevent fake payment completions.
    if not transaction_id:
        logger.error(
            "Viva webhook event 1796 missing TransactionId for order %s "
            "— cannot verify, skipping payment update",
            order.id,
        )
        order.save(update_fields=["metadata"])
        return

    logger.info(
        "Calling Viva Retrieve Transaction API for transaction %s (order %s)",
        transaction_id,
        order.id,
    )
    verified_status, verified_data = _verify_transaction(transaction_id)
    logger.info(
        "Viva Retrieve Transaction result for %s: status=%s | data=%s",
        transaction_id,
        verified_status,
        verified_data,
    )
    if verified_status is None:
        logger.error(
            "Could not verify Viva transaction %s — "
            "leaving event unprocessed so Viva can retry",
            transaction_id,
        )
        # Do NOT save metadata here: we want the event_key to remain
        # unset so Viva's retry delivers the webhook again.
        raise RuntimeError(
            f"Viva transaction verification failed for {transaction_id}"
        )
    if verified_status != PaymentStatus.COMPLETED:
        logger.warning(
            "Viva transaction %s not completed (status: %s) — skipping",
            transaction_id,
            verified_status,
        )
        order.save(update_fields=["metadata"])
        return

    logger.info(
        "Viva transaction %s VERIFIED COMPLETED — updating order %s",
        transaction_id,
        order.id,
    )

    # Capture previous state for audit log before mutating
    previous_payment_status = order.payment_status

    # Set all fields at once to avoid multiple DB writes
    order.metadata["viva_transaction_id"] = transaction_id
    order.payment_id = transaction_id
    order.payment_status = PaymentStatus.COMPLETED
    order.payment_method = "viva_wallet"
    if not order.paid_amount or order.paid_amount.amount == 0:
        order.paid_amount = order.calculate_order_total_amount()

    update_fields = [
        "metadata",
        "payment_id",
        "payment_status",
        "payment_method",
        "paid_amount",
        "paid_amount_currency",
    ]

    if order.status == OrderStatus.PENDING:
        order.status = OrderStatus.PROCESSING
        order.status_updated_at = timezone.now()
        update_fields += ["status", "status_updated_at"]

    order.save(update_fields=update_fields)

    OrderHistory.log_payment_update(
        order=order,
        previous_value={"payment_status": previous_payment_status},
        new_value={
            "payment_status": "completed",
            "payment_id": transaction_id,
            "provider": "viva_wallet",
        },
    )

    from order.payment_events import publish_payment_status

    publish_payment_status(order)

    # Payment verified by Viva Wallet — send the confirmation email now.
    # The task is idempotent (metadata reservation + row lock), so a
    # duplicate webhook delivery or a retry will not resend.
    send_order_confirmation_email.delay(order.id)

    # Enqueue BoxNow delivery-request creation if the order uses a locker.
    from order.enum.shipping_method import OrderShippingMethod

    if order.shipping_method == OrderShippingMethod.BOX_NOW_LOCKER:
        try:
            from shipping_boxnow.tasks import (
                create_boxnow_shipment_for_order,
            )

            create_boxnow_shipment_for_order.delay(order.id)
        except ImportError:
            logger.warning(
                "shipping_boxnow not yet available — "
                "skipping BoxNow task dispatch for order %s",
                order.id,
            )


def _handle_payment_failed(order, event_data, transaction_id):
    logger.info(
        "Viva Wallet payment failed for order %s",
        order.id,
    )

    previous_payment_status = order.payment_status
    order.payment_status = PaymentStatus.FAILED
    order.save(update_fields=["payment_status", "metadata"])

    OrderHistory.log_payment_update(
        order=order,
        previous_value={"payment_status": previous_payment_status},
        new_value={
            "payment_status": "failed",
            "payment_id": transaction_id,
            "provider": "viva_wallet",
        },
    )

    from order.payment_events import publish_payment_status

    publish_payment_status(order)

    # Notify the customer so they can retry instead of silently sitting
    # on a broken order.
    send_payment_failed_email.delay(order.id)


def _handle_reversal_created(order, event_data, transaction_id):
    from django.utils import timezone

    logger.info(
        "Viva Wallet reversal created for order %s",
        order.id,
    )

    previous_payment_status = order.payment_status

    order.payment_status = PaymentStatus.REFUNDED
    order.status = OrderStatus.REFUNDED
    order.status_updated_at = timezone.now()
    order.save(
        update_fields=[
            "payment_status",
            "status",
            "status_updated_at",
            "metadata",
        ]
    )

    OrderHistory.log_payment_update(
        order=order,
        previous_value={
            "payment_status": previous_payment_status,
        },
        new_value={
            "payment_status": "refunded",
            "reversal_transaction_id": transaction_id,
            "provider": "viva_wallet",
        },
    )
