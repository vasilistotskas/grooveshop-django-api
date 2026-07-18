import hashlib
import ipaddress
import json
import logging
from base64 import b64encode

import requests
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
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


def viva_order_code_q(order_code: object) -> Q:
    """Match an Order by ANY Viva orderCode ever issued for it.

    Each ``create_checkout_session`` mints a fresh Viva orderCode and
    appends it to ``metadata['viva_order_codes']`` (the most recent is
    also mirrored in the legacy singular ``metadata['viva_order_code']``
    for the return endpoint's documented ``s`` fallback). A shopper can
    complete payment on an earlier session (stale tab, back button,
    retry) whose orderCode is not the latest, so both the webhook and
    the browser-return lookup MUST resolve any issued code. Matching
    only the latest silently stranded the payment: Viva treats our 200
    as handled and never retries (see
    developer.viva.com/webhooks-for-payments/transaction-payment-created).

    ``metadata__contains`` is the field-level JSONB ``@>`` containment
    lookup (well-supported on PostgreSQL) rather than a key-transform
    ``__contains``.
    """
    code = str(order_code)
    return Q(metadata__viva_order_code=code) | Q(
        metadata__contains={"viva_order_codes": [code]}
    )


# Payment statuses representing a financially settled (terminal) state.
# A stale or out-of-order Viva webhook event MUST NOT overwrite any of these.
_SETTLED_PAYMENT_STATUSES: frozenset[str] = frozenset(
    {
        PaymentStatus.COMPLETED,
        PaymentStatus.REFUNDED,
        PaymentStatus.PARTIALLY_REFUNDED,
        PaymentStatus.CANCELED,
    }
)

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


@csrf_exempt
@require_http_methods(["GET", "POST"])
def viva_wallet_webhook(request):
    if request.method == "GET":
        return _handle_verification(request)
    return _handle_webhook_event(request)


def _webhook_get_rate_limit(request) -> bool:
    """Return True when the request should be blocked.

    Applies a 10-req/hour per-IP cap on the GET verification endpoint.
    Fails open on cache errors so a Redis outage doesn't take down
    Viva's handshake.

    TODO: replace with a strict Viva IP allowlist once the cluster's
    externalTrafficPolicy is set to Local so REMOTE_ADDR carries the
    real Viva IP (currently SNAT-ed to node IP by K3s/Flannel).
    Reference: VIVA_WEBHOOK_IPS_PRODUCTION / VIVA_WEBHOOK_IPS_DEMO.
    """
    ip = request.META.get("HTTP_X_REAL_IP", "").strip() or request.META.get(
        "REMOTE_ADDR", ""
    )
    key = "viva_wh_get:" + hashlib.sha256(ip.encode()).hexdigest()[:24]
    try:
        cache.add(key, 0, 3600)
        count = cache.incr(key)
        return count > 10
    except Exception:
        logger.warning("viva_webhook GET rate-limit: cache error, failing open")
        return False


def _handle_verification(request):
    if _webhook_get_rate_limit(request):
        logger.warning(
            "Viva webhook GET rate limit hit | remote_addr=%s",
            request.META.get("REMOTE_ADDR", ""),
        )
        return JsonResponse({"error": "Too many requests"}, status=429)

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


def _verify_viva_terminal_transaction(
    order, transaction_id, expected_statuses, event_label
):
    """Verify a reversal (1797) / failed (1798) Viva event against the
    Retrieve Transaction API before mutating ``payment_status`` (G0275).

    The webhook endpoint is unauthenticated — there is no HMAC and the
    source-IP check is non-blocking — so the event body must not be trusted
    to flip financial state. A spoofed 1797 could otherwise mark any order
    REFUNDED and fire the refund email + live toast + Meta CAPI Refund;
    a spoofed 1798 could mark it FAILED. Mirroring the 1796 path, we confirm
    with Viva that the transaction genuinely reached the expected terminal
    state.

    Returns ``True`` to proceed. Returns ``False`` (skip, no mutation) when
    the event carries no ``TransactionId`` or the verified status is not one
    we expect. Raises ``RuntimeError`` (→ 500, Viva retries) when
    verification is UNAVAILABLE — including any Retrieve-Transaction error
    such as a 404 for a forged id or a transient network fault — so an
    unverifiable event can never mutate state on a trusted-by-default basis.
    """
    if not transaction_id:
        logger.error(
            "Viva %s event for order %s carries no TransactionId — refusing "
            "to mutate payment state without verification",
            event_label,
            order.id,
        )
        return False

    verified_status, verified_data = _verify_transaction(transaction_id)
    verify_errored = isinstance(verified_data, dict) and (
        verified_data.get("error") or verified_data.get("viva_error")
    )
    if verified_status is None or verify_errored:
        # Verification infrastructure failed, or Viva could not cleanly
        # retrieve the transaction (a forged/unknown id returns an error row,
        # and get_payment_status maps errors to FAILED — which would
        # otherwise auto-satisfy the failed check). Roll back and retry
        # rather than trust the unverified event.
        raise RuntimeError(
            f"Viva transaction verification unavailable for {transaction_id}"
        )

    if verified_status not in expected_statuses:
        logger.warning(
            "Viva %s event for order %s: transaction %s verified status is "
            "%s, not in %s — skipping (event unverified or premature)",
            event_label,
            order.id,
            transaction_id,
            verified_status,
            sorted(expected_statuses),
        )
        return False

    return True


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

    event_data = body.get("EventData", {})
    event_type_id = body.get("EventTypeId")

    transaction_id = event_data.get("TransactionId", "")
    order_code = event_data.get("OrderCode")
    status_id = event_data.get("StatusId", "")

    # Allowlisted log to avoid persisting PII (customer name, email,
    # transaction details, amounts) into structured log aggregation —
    # GDPR Art. 32. ``transaction_id`` is hashed because it can be
    # replayed against Viva's Retrieve Transaction API.
    txn_hash = (
        hashlib.sha256(str(transaction_id).encode()).hexdigest()[:16]
        if transaction_id
        else ""
    )
    logger.info(
        "Viva webhook payload | event_type=%s | order_code=%s | "
        "status_id=%s | txn_hash=%s",
        event_type_id,
        order_code,
        status_id,
        txn_hash,
    )

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

    order = Order.objects.filter(viva_order_code_q(order_code)).first()

    if not order:
        logger.error(
            "Order not found for Viva Wallet order code: %s | "
            "(searched metadata.viva_order_code + viva_order_codes[])",
            order_code,
        )
        return JsonResponse({"status": "ok"})

    logger.info(
        "Viva webhook matched order #%s (uuid=%s, status=%s, payment_status=%s)",
        order.id,
        order.uuid,
        order.status,
        order.payment_status,
    )

    # Idempotency: ``VivaWebhookEvent`` table is the single source of
    # truth. The unique ``(transaction_id, event_type_id)`` constraint
    # blocks replays at the DB level — admin metadata edits cannot
    # reopen the door.
    from order.models.viva_webhook_event import VivaWebhookEvent

    if (
        transaction_id
        and event_type_id is not None
        and VivaWebhookEvent.objects.filter(
            transaction_id=transaction_id, event_type_id=event_type_id
        ).exists()
    ):
        logger.info(
            "Viva Wallet webhook already processed | event_type=%s | "
            "txn_hash=%s (idempotency hit)",
            event_type_id,
            txn_hash,
        )
        return JsonResponse({"status": "ok"})

    try:
        with transaction.atomic():
            # Re-fetch with row lock to prevent race conditions
            order = Order.objects.select_for_update().get(pk=order.pk)

            # Double-check idempotency after acquiring lock
            if (
                transaction_id
                and event_type_id is not None
                and VivaWebhookEvent.objects.filter(
                    transaction_id=transaction_id, event_type_id=event_type_id
                ).exists()
            ):
                logger.info(
                    "Viva webhook event_type=%s txn_hash=%s processed by "
                    "parallel request — skipping",
                    event_type_id,
                    txn_hash,
                )
                return JsonResponse({"status": "ok"})

            # Event type IDs per Viva documentation:
            # 1796 = Transaction Payment Created
            # 1797 = Transaction Reversal Created
            # 1798 = Transaction Failed
            logger.info(
                "Viva webhook dispatching event_type=%s for order #%s",
                event_type_id,
                order.id,
            )
            outcome = VivaWebhookEvent.OUTCOME_PROCESSED
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
                outcome = VivaWebhookEvent.OUTCOME_SKIPPED

            # Persist the idempotency row last — if any handler raised
            # the row was never written, Viva retries, the next attempt
            # gets a fresh shot. We only record events that have both
            # keys; an empty ``transaction_id`` would collapse every
            # payload-less event onto one row.
            if transaction_id and event_type_id is not None:
                VivaWebhookEvent.objects.create(
                    transaction_id=str(transaction_id),
                    event_type_id=event_type_id,
                    order=order,
                    order_code=str(order_code or ""),
                    status_id=str(status_id or ""),
                    outcome=outcome,
                )
    except RuntimeError as exc:
        # Raised by _handle_payment_created when Viva's verification API
        # is unreachable. Returning 500 signals Viva to retry the webhook;
        # the VivaWebhookEvent row is NOT persisted (transaction rolled
        # back) so the retry will be processed fresh.
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
        # Raise so the outer atomic block rolls back; the
        # VivaWebhookEvent row is never written, Viva retries fresh.
        raise RuntimeError(
            f"Viva transaction verification failed for {transaction_id}"
        )
    # Defence in depth: confirm the verified transaction amount matches
    # this order's total. Without this check an attacker who knows their
    # own valid TransactionId and another user's OrderCode could replay
    # a low-value transaction against a high-value order — the IP gate
    # is non-blocking and the Retrieve Transaction API only proves the
    # transaction exists at Viva, not that it was for this order.
    verified_amount_raw = (
        verified_data.get("amount") if isinstance(verified_data, dict) else None
    )
    if verified_amount_raw is not None:
        try:
            from decimal import Decimal  # noqa: PLC0415

            verified_amount = Decimal(str(verified_amount_raw))
            expected_amount = order.calculate_order_total_amount().amount
            # Allow a 1-cent tolerance for any provider-side rounding.
            if abs(verified_amount - expected_amount) > Decimal("0.01"):
                logger.error(
                    "Viva transaction %s amount mismatch: verified=%s "
                    "expected=%s for order %s — refusing to mark as paid",
                    transaction_id,
                    verified_amount,
                    expected_amount,
                    order.id,
                )
                return
        except (TypeError, ValueError, AttributeError):
            logger.warning(
                "Could not parse Viva verified amount %r for order %s — "
                "proceeding with status-only verification.",
                verified_amount_raw,
                order.id,
            )

    if verified_status != PaymentStatus.COMPLETED:
        logger.warning(
            "Viva transaction %s not completed (status: %s) — skipping",
            transaction_id,
            verified_status,
        )
        return

    logger.info(
        "Viva transaction %s VERIFIED COMPLETED — updating order %s",
        transaction_id,
        order.id,
    )

    # Guard: a stale or out-of-order Viva webhook must not un-refund or
    # un-cancel an order that is already in a settled financial state.
    # Viva does NOT guarantee delivery order.  COMPLETED is allowed
    # through (idempotent — the PENDING→PROCESSING block below is gated
    # on order.status so no double-shipment dispatch occurs).
    _refund_or_cancel = {
        PaymentStatus.REFUNDED,
        PaymentStatus.PARTIALLY_REFUNDED,
        PaymentStatus.CANCELED,
    }
    if order.payment_status in _refund_or_cancel:
        logger.warning(
            "Ignoring stale payment_created (Viva) for order %s: "
            "payment_status already %s",
            order.id,
            order.payment_status,
        )
        return

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
        # Mirror the Stripe handler's PR #7 suppression: the Viva
        # webhook dispatches ``send_order_confirmation_email`` directly
        # below — that already conveys "your order is being processed".
        # Without this pre-stamp the post-save signal would fire a
        # second PROCESSING email + toast within ms of the
        # confirmation email.
        from order.services import OrderService

        OrderService._suppress_customer_status_notifications(
            order, OrderStatus.PROCESSING.value
        )
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
    # Wrapped in on_commit so the Celery worker always sees the committed
    # payment_status / order.status rather than an in-flight row.
    transaction.on_commit(
        lambda oid=order.id: send_order_confirmation_email.delay(oid)
    )

    # Enqueue the carrier's delivery-request creation. Provider-agnostic
    # dispatch through the registry — Stripe's ``handle_payment_succeeded``
    # uses the same hook. Without this call, ACS (and any future
    # carrier) orders paid via Viva would never get their shipment task
    # fired on payment success, leaving the order stuck in PROCESSING
    # with no voucher / no parcel.
    from shipping.services import ShippingService

    ShippingService.dispatch_create_shipment_task(order)


def _handle_payment_failed(order, event_data, transaction_id):
    logger.info(
        "Viva Wallet payment failed for order %s",
        order.id,
    )

    # Guard: a stale or out-of-order "payment failed" Viva event must not
    # overwrite a financially settled state.  Viva does NOT guarantee
    # delivery order.
    if order.payment_status in _SETTLED_PAYMENT_STATUSES:
        logger.warning(
            "Ignoring stale payment_failed (Viva) for order %s: "
            "payment_status already %s",
            order.id,
            order.payment_status,
        )
        return

    # Never trust the unauthenticated event body: confirm with Viva that the
    # transaction actually failed before flipping state (G0275).
    if not _verify_viva_terminal_transaction(
        order,
        transaction_id,
        {PaymentStatus.FAILED, PaymentStatus.CANCELED},
        "payment_failed",
    ):
        return

    previous_payment_status = order.payment_status
    order.payment_status = PaymentStatus.FAILED
    order.save(update_fields=["payment_status"])

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
    # Wrapped in on_commit so the worker sees the committed payment_status.
    transaction.on_commit(
        lambda oid=order.id: send_payment_failed_email.delay(oid)
    )


def _handle_reversal_created(order, event_data, transaction_id):
    """Mirrors ``handle_stripe_charge_refunded``: only ``payment_status``
    transitions, an audit row lands in ``metadata['refunds']``, and
    ``order_refunded`` fires so the refund email, live WS toast, and Meta
    CAPI Refund event all run. ``Order.status`` is left untouched —
    deciding whether a reversal also means the goods are returned is a
    business call the admin owns.
    """
    from order.signals import order_refunded

    logger.info(
        "Viva Wallet reversal created for order %s",
        order.id,
    )

    # Never trust the unauthenticated event body: confirm with Viva that the
    # transaction was actually reversed/refunded before marking the order
    # REFUNDED and firing the refund email + toast + Meta CAPI Refund (G0275).
    if not _verify_viva_terminal_transaction(
        order,
        transaction_id,
        {PaymentStatus.REFUNDED, PaymentStatus.PARTIALLY_REFUNDED},
        "reversal",
    ):
        return

    previous_payment_status = order.payment_status

    if not order.metadata:
        order.metadata = {}
    refunds = list(order.metadata.get("refunds") or [])
    refunds.append(
        {
            "reversal_transaction_id": transaction_id,
            "provider": "viva_wallet",
        }
    )
    order.metadata["refunds"] = refunds
    order.payment_status = PaymentStatus.REFUNDED

    order.save(update_fields=["payment_status", "metadata"])

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

    order_refunded.send(sender=Order, order=order)
