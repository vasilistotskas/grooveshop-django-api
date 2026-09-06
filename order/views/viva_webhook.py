import hashlib
import ipaddress
import json
import logging
from base64 import b64encode
from decimal import InvalidOperation
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tenant.credentials import VivaWalletCredentials

import requests
from django.core.cache import cache
from django.db import connection, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_tenants.utils import (
    get_public_schema_name,
    schema_context,
    tenant_context,
)

from order.enum.status import (
    SETTLED_PAYMENT_STATUSES,
    OrderStatus,
    PaymentStatus,
)
from order.models.history import OrderHistory
from order.models.order import AMOUNT_MISMATCH_FLAG, Order
from order.tasks import (
    send_order_confirmation_email,
    send_payment_failed_email,
)
from tenant.celery import dispatch_on_commit


def _resolve_tenant_candidates(order_code: str) -> list:
    """Every active, non-suspended tenant whose Order matches order_code.

    Viva webhooks land in the public schema (no tenant routing exists at
    the HTTP layer for machine-to-machine callers), so ownership is
    found by iterating tenants and looking the order up via
    ``viva_order_code_q`` — matching both the latest
    the ``viva_order_codes`` history
    array, because every ``create_checkout_session`` mints a fresh code
    and a shopper on a stale tab may pay an earlier one.

    Returns ALL matches, not the first, because the order code lives in
    merchant-editable ``Order.metadata`` and is therefore NOT proof of
    ownership: a merchant could plant a rival's order code in one of
    their own orders. When more than one tenant matches, the caller
    disambiguates by Viva-credential verification — only the tenant
    whose Viva account actually holds the transaction can retrieve it —
    and processes in the first candidate that verifies (see
    ``_handle_webhook_event``). Suspended/inactive tenants are excluded:
    a re-delivery for a frozen tenant must not mutate its data; that
    case is handled separately by ``_order_exists_on_unavailable_tenant``.

    Real ``Tenant`` instances (not schema names) so callers enter them
    via ``tenant_context(tenant)`` — ``schema_context(schema_name)``
    only sets a bare ``FakeTenant`` on ``connection.tenant``, which
    breaks every ``viva_wallet_credentials()`` read (tenant-only, no
    settings fallback), including the Retrieve-Transaction call that
    authenticates the webhook.
    """
    if not order_code:
        return []

    from giftcard.models import GiftCardPurchase
    from tenant.models import Tenant

    public = get_public_schema_name()
    candidates = []
    for tenant in Tenant.objects.filter(
        is_active=True, suspended_at__isnull=True
    ).exclude(schema_name=public):
        with schema_context(tenant.schema_name):
            if (
                Order.objects.filter(viva_order_code_q(order_code)).exists()
                or GiftCardPurchase.objects.filter(
                    payment_id=str(order_code),
                    provider_code="viva_wallet",
                ).exists()
            ):
                candidates.append(tenant)
    return candidates


def _resolve_tenant_for_order_code(order_code: str):
    """First tenant owning ``order_code`` (or ``None``).

    Thin wrapper over ``_resolve_tenant_candidates`` for callers/tests
    that only need presence. The webhook handler itself uses the full
    candidate list so it can verify-then-select across a code collision.
    """
    candidates = _resolve_tenant_candidates(order_code)
    return candidates[0] if candidates else None


def _order_exists_on_unavailable_tenant(order_code: object) -> bool:
    """True when the order lives on a suspended/inactive tenant.

    Separates "come back later" from "this code means nothing here".
    The caller must NOT acknowledge the first case: Viva only redelivers
    on a non-2xx, so a 200 drops the event permanently — the shopper has
    paid, the order stays PENDING, and 24h later
    ``auto_cancel_stuck_pending_orders`` cancels it with
    ``refund_payment=False``, restores stock and emails the customer
    that their order was cancelled. The money sits in the merchant's
    Viva account with no order and no alert. Unlike BoxNow there is no
    polling job to rescue it.
    """
    if not order_code:
        return False

    from tenant.models import Tenant

    public = get_public_schema_name()
    unavailable = Tenant.objects.filter(
        is_active=False
    ) | Tenant.objects.filter(suspended_at__isnull=False)
    for tenant in unavailable.exclude(schema_name=public).distinct():
        try:
            with schema_context(tenant.schema_name):
                from giftcard.models import (
                    GiftCardPurchase,
                )

                if (
                    Order.objects.filter(viva_order_code_q(order_code)).exists()
                    or GiftCardPurchase.objects.filter(
                        payment_id=str(order_code),
                        provider_code="viva_wallet",
                    ).exists()
                ):
                    return True
        except Exception:
            # A destroyed tenant's schema may be gone already; that is not
            # a "retry later" case, so the loop moves on. It does not move
            # on QUIETLY though: this loop is how an unauthenticated
            # webhook finds its tenant, and a tenant skipped because of an
            # unexpected error is otherwise indistinguishable from one
            # that simply did not hold the order code.
            logger.warning(
                "Viva webhook: skipped tenant %s while resolving order "
                "code %s — its schema may already be gone",
                getattr(tenant, "schema_name", tenant),
                order_code,
                exc_info=True,
            )
            continue
    return False


logger = logging.getLogger(__name__)


def viva_order_code_q(order_code: object) -> Q:
    """Match an Order by ANY Viva orderCode ever issued for it.

    Each ``create_checkout_session`` mints a fresh Viva orderCode and
    appends it to ``metadata['viva_order_codes']``. A shopper can
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
    return Q(metadata__contains={"viva_order_codes": [str(order_code)]})


# Payment statuses representing a financially settled (terminal) state.
# A stale or out-of-order Viva webhook event MUST NOT overwrite any of these.

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
    from tenant.credentials import viva_wallet_credentials

    creds = viva_wallet_credentials()
    verification_key = creds["webhook_verification_key"]

    if verification_key:
        logger.info("Using configured Viva Wallet webhook verification key")
    else:
        logger.info(
            "Viva Wallet webhook verification key not set — fetching from Viva"
        )
        verification_key = _fetch_verification_key(creds)

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


def _fetch_verification_key(
    creds: VivaWalletCredentials | None = None,
):
    from tenant.credentials import viva_wallet_credentials

    if creds is None:
        creds = viva_wallet_credentials()

    merchant_id = creds["merchant_id"]
    api_key = creds["api_key"]

    if not merchant_id or not api_key:
        logger.error("Viva Wallet merchant_id or api_key not configured")
        return ""

    # Per-tenant: the tenant's own Viva account decides demo vs live.
    live_mode = creds["live_mode"]
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

    Caller must already be inside the resolved tenant's
    ``schema_context`` so ``live_mode`` reflects THIS tenant's Viva
    account — there is no platform-wide live/demo mode setting.
    """
    from tenant.credentials import viva_wallet_credentials

    live_mode = viva_wallet_credentials()["live_mode"]
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
    except Exception:
        logger.exception("_verify_transaction: FAILED for %s", transaction_id)
        return None, {}


def _verify_viva_terminal_transaction(
    subject, transaction_id, expected_statuses, event_label, *, order=None
):
    """Verify a reversal (1797) / failed (1798) Viva event against the
    Retrieve Transaction API before mutating financial state (G0275).

    The webhook endpoint is unauthenticated — there is no HMAC and the
    source-IP check is non-blocking — so the event body must not be trusted
    to flip financial state. A spoofed 1797 could otherwise mark any order
    REFUNDED and fire the refund email + live toast + Meta CAPI Refund, or
    void a paid-for gift card; a spoofed 1798 could mark either FAILED.
    Mirroring the 1796 path, we confirm with Viva that the transaction
    genuinely reached the expected terminal state.

    *subject* is a human label for the thing being mutated ("order 42",
    "gift-card purchase <uuid>") and is only used in the log lines: this
    guard is shared by the order and gift-card branches, which have no
    common model.

    *subject* is a human label for the thing being mutated ("order 42",
    "gift-card purchase <uuid>") and is used only in the log lines, so this
    guard can be shared by callers that have no order model.

    *order*, when given, additionally requires the verified transaction to
    carry one of THAT order's own Viva order codes. Viva's instruction is
    to confirm a result by the COMBINATION of OrderCode and TransactionId;
    a caller with no order passes nothing and gets the status/amount half
    only.

    Returns ``True`` to proceed. Returns ``False`` (skip, no mutation) when
    the event carries no ``TransactionId`` or the verified status is not one
    we expect. Raises ``RuntimeError`` (→ 500, Viva retries) when
    verification is UNAVAILABLE — including any Retrieve-Transaction error
    such as a 404 for a forged id or a transient network fault — so an
    unverifiable event can never mutate state on a trusted-by-default basis.
    """
    if not transaction_id:
        logger.error(
            "Viva %s event for %s carries no TransactionId — refusing "
            "to mutate payment state without verification",
            event_label,
            subject,
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

    # Viva's own instruction is to confirm the result with the
    # COMBINATION of OrderCode and TransactionId
    # (developer.viva.com/webhooks-for-payments/transaction-payment-created),
    # then check StatusId and Amount. Without the first half, anyone can
    # post their own real TransactionId against someone else's OrderCode.
    if order is not None and not _transaction_belongs_to_order(
        order, verified_data
    ):
        logger.error(
            "Viva %s event: transaction %s reports order_code %r, which is "
            "not one of %s's issued codes — refusing to mutate state",
            event_label,
            transaction_id,
            verified_data.get("order_code")
            if isinstance(verified_data, dict)
            else None,
            subject,
        )
        return False

    if verified_status not in expected_statuses:
        logger.warning(
            "Viva %s event for %s: transaction %s verified status is "
            "%s, not in %s — skipping (event unverified or premature)",
            event_label,
            subject,
            transaction_id,
            verified_status,
            sorted(expected_statuses),
        )
        return False

    return True


def _handle_webhook_event(request):

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

    # Resolve the owning tenant. Every ORM call below must run inside
    # that tenant's schema_context — otherwise we'd query the public
    # schema (where Order rows do not live) and silently drop the
    # webhook on the floor.
    candidates = _resolve_tenant_candidates(order_code)
    if not candidates:
        if _order_exists_on_unavailable_tenant(order_code):
            # Refuse to acknowledge so Viva redelivers once the operator
            # reactivates the tenant. Acknowledging would drop a PAID
            # order on the floor — see the helper's docstring.
            logger.error(
                "Viva webhook for order code %s belongs to a suspended or "
                "inactive tenant — refusing to acknowledge so Viva "
                "redelivers. Reactivate the tenant within 24h or the "
                "order is auto-cancelled WITHOUT refund.",
                order_code,
            )
            return JsonResponse(
                {"status": "tenant_unavailable"},
                status=503,
            )
        logger.error(
            "Order not found for Viva Wallet order code: %s | "
            "(no tenant matched metadata.viva_order_codes + "
            "viva_order_codes[])",
            order_code,
        )
        return JsonResponse({"status": "ok"})

    # Verification-driven selection across an order-code collision.
    #
    # The order code is merchant-editable metadata, so a single match is
    # not proof of ownership. Process in the tenant whose Viva
    # credentials actually VERIFY the transaction: a candidate that does
    # not own it fails the Retrieve-Transaction call, and
    # ``_process_event_in_tenant`` returns 500 with its atomic block
    # rolled back (no side effects), so we move to the next candidate.
    # The true owner verifies and returns 200. If EVERY candidate
    # returns 500 — all wrong, or the real owner's verification is
    # transiently unavailable — we return that 500 so Viva redelivers,
    # exactly as the single-tenant path always has.
    #
    # Each candidate is entered via ``tenant_context(tenant)`` (not
    # ``schema_context(schema_name)``) so ``connection.tenant`` is the
    # REAL row: ``viva_wallet_credentials()`` (tenant-only, no settings
    # fallback) needs the real fields, including inside
    # ``VivaWalletPaymentProvider.__init__`` which authenticates this
    # very webhook.
    multi = len(candidates) > 1
    last_response = None
    for tenant in candidates:
        with tenant_context(tenant):
            # Best-effort IP check — informational only; authentication
            # is the Retrieve-Transaction call. Runs inside the tenant's
            # context so ``live_mode`` reflects THIS tenant's Viva
            # account, not demo.
            ip_match, observed_ip = _check_source_ip(request)
            if ip_match:
                logger.info(
                    "Viva webhook IP %s matches Viva range", observed_ip
                )
            else:
                logger.info(
                    "Viva webhook from non-Viva IP %s — will rely on "
                    "transaction API verification (expected behind SNAT'd "
                    "ingress)",
                    observed_ip,
                )

            response = _process_event_in_tenant(
                order_code=order_code,
                event_type_id=event_type_id,
                event_data=event_data,
                transaction_id=transaction_id,
                status_id=status_id,
                txn_hash=txn_hash,
            )

        if response.status_code != 500:
            if multi:
                logger.warning(
                    "Viva webhook: order code %s matched %d tenants; "
                    "processed in the one that verified (schema=%s)",
                    order_code,
                    len(candidates),
                    tenant.schema_name,
                )
            return response
        last_response = response
        if multi:
            # Only interesting on a collision — the single-tenant path
            # 500s for ordinary transient verification failures too.
            logger.warning(
                "Viva webhook: candidate tenant schema=%s did not verify "
                "the transaction for order code %s — trying next candidate",
                tenant.schema_name,
                order_code,
            )

    if multi:
        logger.error(
            "Viva webhook: order code %s matched %d tenants but NONE "
            "verified the transaction — returning 500 so Viva retries",
            order_code,
            len(candidates),
        )
    return last_response


def _process_event_in_tenant(
    *,
    order_code: str,
    event_type_id,
    event_data: dict,
    transaction_id: str,
    status_id: str,
    txn_hash: str,
) -> JsonResponse:
    """Run the Viva webhook state-machine inside an active tenant schema.

    Caller must already be inside ``tenant_context(tenant)``.
    """
    # Gift-card purchases are NOT orders — their Viva orderCode lives on
    # ``GiftCardPurchase.payment_id``. Resolve them first: the same
    # verify-then-select contract applies (an unverifiable transaction
    # raises so the outer loop tries the next tenant candidate).
    from giftcard.models import GiftCardPurchase

    purchase = GiftCardPurchase.objects.filter(
        payment_id=str(order_code), provider_code="viva_wallet"
    ).first()
    if purchase is not None:
        return _process_gift_card_purchase_event(
            purchase=purchase,
            event_type_id=event_type_id,
            event_data=event_data,
            transaction_id=transaction_id,
            txn_hash=txn_hash,
            order_code=order_code,
        )

    order = Order.objects.filter(viva_order_code_q(order_code)).first()

    if not order:
        logger.error(
            "Viva webhook: tenant schema=%s resolved but Order vanished "
            "(order_code=%s, searched metadata.viva_order_codes + "
            "viva_order_codes[])",
            connection.schema_name,
            order_code,
        )
        return JsonResponse({"status": "ok"})

    logger.info(
        "Viva webhook matched order #%s (uuid=%s, status=%s, "
        "payment_status=%s) in tenant=%s",
        order.id,
        order.uuid,
        order.status,
        order.payment_status,
        connection.schema_name,
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
                # The handler reports a skip so the audit row does not
                # claim work that never happened.
                outcome = (
                    _handle_payment_created(order, event_data, transaction_id)
                    or VivaWebhookEvent.OUTCOME_PROCESSED
                )
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


def _process_gift_card_purchase_event(
    *,
    purchase,
    event_type_id,
    event_data: dict,
    transaction_id: str,
    txn_hash: str,
    order_code: str,
) -> JsonResponse:
    """Viva webhook state-machine for a gift-card PURCHASE.

    Mirrors the order path's guarantees: Retrieve-Transaction
    verification (an error raises so the multi-tenant candidate loop
    can try the true owner), a strict amount guard against the
    purchase value, and ``VivaWebhookEvent`` DB-level idempotency.
    Completion itself is additionally idempotent via the purchase
    status guard in ``GiftCardService.complete_purchase``.
    """
    from decimal import Decimal as _Decimal

    from giftcard.enum import GiftCardPurchaseStatus
    from giftcard.services import GiftCardService
    from order.models.viva_webhook_event import VivaWebhookEvent

    if (
        transaction_id
        and event_type_id is not None
        and VivaWebhookEvent.objects.filter(
            transaction_id=transaction_id, event_type_id=event_type_id
        ).exists()
    ):
        logger.info(
            "Viva gift-card webhook already processed | event_type=%s | "
            "txn_hash=%s (idempotency hit)",
            event_type_id,
            txn_hash,
        )
        return JsonResponse({"status": "ok"})

    try:
        with transaction.atomic():
            purchase = (
                type(purchase).objects.select_for_update().get(pk=purchase.pk)
            )

            outcome = VivaWebhookEvent.OUTCOME_PROCESSED
            if event_type_id == 1796:
                status_id = event_data.get("StatusId", "")
                if status_id and status_id != "F":
                    outcome = VivaWebhookEvent.OUTCOME_SKIPPED
                elif not transaction_id:
                    logger.error(
                        "Viva gift-card event 1796 without TransactionId "
                        "for purchase %s — cannot verify, skipping",
                        purchase.uuid,
                    )
                    outcome = VivaWebhookEvent.OUTCOME_SKIPPED
                else:
                    verified_status, verified_data = _verify_transaction(
                        transaction_id
                    )
                    verify_errored = isinstance(verified_data, dict) and (
                        verified_data.get("error")
                        or verified_data.get("viva_error")
                    )
                    if verified_status is None or verify_errored:
                        # Unverifiable ≠ failed: raise so the outer
                        # candidate loop tries the next tenant / Viva
                        # redelivers (same contract as the order path).
                        raise RuntimeError(
                            "Viva transaction verification unavailable "
                            f"for gift-card purchase txn {transaction_id}"
                        )
                    verified_amount_raw = (
                        verified_data.get("amount")
                        if isinstance(verified_data, dict)
                        else None
                    )
                    if verified_amount_raw is not None:
                        verified_amount = _Decimal(str(verified_amount_raw))
                        expected = _Decimal(str(purchase.amount.amount))
                        if abs(verified_amount - expected) > _Decimal("0.01"):
                            logger.error(
                                "Viva gift-card txn %s amount mismatch: "
                                "verified=%s expected=%s (purchase %s) — "
                                "refusing to complete",
                                transaction_id,
                                verified_amount,
                                expected,
                                purchase.uuid,
                            )
                            outcome = VivaWebhookEvent.OUTCOME_SKIPPED
                            verified_status = None
                    if verified_status == PaymentStatus.COMPLETED:
                        GiftCardService.complete_purchase(purchase)
                        logger.info(
                            "Gift card purchase %s completed via Viva "
                            "webhook (txn_hash=%s)",
                            purchase.uuid,
                            txn_hash,
                        )
                    elif outcome == VivaWebhookEvent.OUTCOME_PROCESSED:
                        logger.warning(
                            "Viva gift-card txn %s not completed "
                            "(status=%s) — skipping",
                            transaction_id,
                            verified_status,
                        )
                        outcome = VivaWebhookEvent.OUTCOME_SKIPPED
            elif event_type_id == 1798:
                if (
                    purchase.status != GiftCardPurchaseStatus.PENDING
                    or not _verify_viva_terminal_transaction(
                        f"gift-card purchase {purchase.uuid}",
                        transaction_id,
                        {PaymentStatus.FAILED, PaymentStatus.CANCELED},
                        "payment_failed",
                    )
                ):
                    outcome = VivaWebhookEvent.OUTCOME_SKIPPED
                else:
                    purchase.status = GiftCardPurchaseStatus.FAILED
                    purchase.save(update_fields=["status"])
                    logger.info(
                        "Gift card purchase %s marked FAILED via Viva webhook",
                        purchase.uuid,
                    )
            elif event_type_id == 1797:
                # A reversal DESTROYS stored value — it cancels the purchase
                # and voids every untouched card it issued. The event body is
                # unauthenticated, and the Viva order code that resolves the
                # purchase is visible to the buyer in the checkout URL, so
                # this must never run on the event's say-so.
                if not _verify_viva_terminal_transaction(
                    f"gift-card purchase {purchase.uuid}",
                    transaction_id,
                    {
                        PaymentStatus.REFUNDED,
                        PaymentStatus.PARTIALLY_REFUNDED,
                    },
                    "reversal",
                ):
                    outcome = VivaWebhookEvent.OUTCOME_SKIPPED
                else:
                    outcome = GiftCardService.handle_purchase_reversal(purchase)
            else:
                logger.info(
                    "Unhandled Viva event type %s for gift-card purchase %s",
                    event_type_id,
                    purchase.uuid,
                )
                outcome = VivaWebhookEvent.OUTCOME_SKIPPED

            if transaction_id and event_type_id is not None:
                VivaWebhookEvent.objects.create(
                    transaction_id=transaction_id,
                    event_type_id=event_type_id,
                    order=None,
                    order_code=str(order_code),
                    status_id=event_data.get("StatusId", "") or "",
                    outcome=outcome,
                )
    except Exception:
        logger.exception(
            "Viva gift-card webhook processing failed for purchase %s",
            purchase.uuid,
        )
        return JsonResponse({"error": "processing failed"}, status=500)

    return JsonResponse({"status": "ok"})


def order_viva_codes(order) -> set[str]:
    """Every Viva orderCode ever issued for this order.

    The mirror of :func:`viva_order_code_q`, evaluated in Python: each
    ``create_checkout_session`` mints a fresh code and appends it to
    ``metadata['viva_order_codes']``, with the most recent also mirrored
    in the singular key.
    """
    metadata = order.metadata or {}
    codes = {
        str(code) for code in (metadata.get("viva_order_codes") or []) if code
    }
    # ``_resolve_tenant_candidates`` also resolves an order by
    # ``payment_id``, so a code stored there counts as issued too — the
    # two must agree on what "belongs to this order" means.
    if order.payment_id:
        codes.add(str(order.payment_id))
    return codes


def _transaction_belongs_to_order(order, verified_data) -> bool:
    """Does the VERIFIED transaction actually belong to this order?

    Viva's own guidance is to retrieve the transaction and validate the
    orderCode, statusId AND amount from that response
    (developer.viva.com/webhooks-for-payments/setting-up-webhooks). We
    checked statusId and amount but only logged the orderCode, which
    leaves the substitution the amount guard cannot see: the webhook body
    is unauthenticated, so anyone may post their OWN real TransactionId
    against SOMEONE ELSE'S OrderCode. We would resolve the victim's
    order, verify the attacker's transaction — genuinely COMPLETED — and
    settle the victim's order as soon as the two amounts happen to
    match, which costs the attacker nothing to arrange.

    An absent orderCode in the response is NOT treated as a match: the
    whole point is to confirm the link, and a confirmation we did not
    receive is not one we can assume.
    """
    reported = (
        verified_data.get("order_code")
        if isinstance(verified_data, dict)
        else None
    )
    if not reported:
        # Viva documents orderCode as part of the Retrieve Transaction
        # response, so its absence is an abnormal answer rather than a
        # mismatch. Raise instead of refusing: a 500 has Viva redeliver,
        # where a skip would ack the event and strand a real payment.
        raise RuntimeError(
            f"Viva transaction {verified_data.get('payment_id')!r} was "
            "verified without an orderCode — cannot confirm it belongs to "
            f"order {order.id}"
        )
    return str(reported) in order_viva_codes(order)


def _flag_amount_mismatch(
    order, transaction_id, verified_amount, expected_amount
) -> None:
    """Record a verified charge whose amount does not match the order."""
    from django.utils import timezone

    if not order.metadata:
        order.metadata = {}
    order.metadata[AMOUNT_MISMATCH_FLAG] = {
        "transaction_id": str(transaction_id),
        "verified_amount": str(verified_amount),
        "expected_amount": str(expected_amount),
        "observed_at": timezone.now().isoformat(),
    }
    order.save(update_fields=["metadata"])


def _handle_payment_created(order, event_data, transaction_id):
    from django.utils import timezone

    from order.models.viva_webhook_event import (
        VivaWebhookEvent,
    )

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
    # Allowlisted fields only — never log the raw provider dict (it may
    # carry cardholder data; see the redaction policy at the top of the
    # webhook handler).
    logger.info(
        "Viva Retrieve Transaction result for %s: status=%s "
        "raw_status=%s amount=%s order_code=%s",
        transaction_id,
        verified_status,
        verified_data.get("raw_status")
        if isinstance(verified_data, dict)
        else None,
        verified_data.get("amount")
        if isinstance(verified_data, dict)
        else None,
        verified_data.get("order_code")
        if isinstance(verified_data, dict)
        else None,
    )
    verify_errored = isinstance(verified_data, dict) and (
        verified_data.get("error") or verified_data.get("viva_error")
    )
    if verified_status is None or verify_errored:
        logger.error(
            "Could not verify Viva transaction %s (status=%s, errored=%s) "
            "— leaving event unprocessed so Viva can retry",
            transaction_id,
            verified_status,
            bool(verify_errored),
        )
        # Raise so the outer atomic block rolls back; the
        # VivaWebhookEvent row is never written, Viva retries fresh.
        #
        # Treating a Retrieve-Transaction ERROR (``error`` / ``viva_error``
        # — e.g. a 404 because the id is not in THIS tenant's Viva account)
        # as "unavailable" rather than as a confirmed non-completion is
        # what lets a webhook whose transaction belongs to a DIFFERENT
        # tenant fall through to the next candidate in
        # ``_handle_webhook_event`` — and, independent of multi-tenancy,
        # stops a transient Viva error from being silently acknowledged
        # as "payment not completed". Mirrors
        # ``_verify_viva_terminal_transaction`` (1797/1798).
        raise RuntimeError(
            f"Viva transaction verification unavailable for {transaction_id}"
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
    # The transaction must be THIS order's before its amount or status
    # mean anything (Viva's documented three-way check).
    if not _transaction_belongs_to_order(order, verified_data):
        logger.error(
            "Viva transaction %s reports order_code %r, which is not one of "
            "order %s's issued codes — refusing to mark as paid",
            transaction_id,
            verified_data.get("order_code")
            if isinstance(verified_data, dict)
            else None,
            order.id,
        )
        return VivaWebhookEvent.OUTCOME_SKIPPED

    # Currency first: an amount only means something once we know it is
    # denominated in the order's currency. Viva reports ISO 4217 in its
    # NUMERIC form ("978"); the provider normalises that to the
    # alphabetic code, so this compares like with like.
    order_total = order.calculate_order_total_amount()
    verified_currency = (
        verified_data.get("currency")
        if isinstance(verified_data, dict)
        else None
    )
    if verified_currency and verified_currency != order_total.currency.code:
        logger.error(
            "Viva transaction %s is in %s but order %s is in %s — refusing "
            "to mark as paid",
            transaction_id,
            verified_currency,
            order.id,
            order_total.currency.code,
        )
        _flag_amount_mismatch(
            order, transaction_id, verified_currency, order_total.currency.code
        )
        return VivaWebhookEvent.OUTCOME_SKIPPED

    if verified_amount_raw is not None:
        try:
            from decimal import Decimal

            verified_amount = Decimal(str(verified_amount_raw))
            expected_amount = order_total.amount
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
                # Marking it paid would under-charge, so we do not — but
                # the money HAS left the customer (Viva verified a real
                # transaction), and the usual cause is a shopper paying
                # on a stale checkout tab after the total moved. Record
                # it on the order: without this the order just sat
                # PENDING and auto_cancel_stuck_pending_orders closed it
                # a day later with refund_payment=False, leaving a
                # charged customer, a cancelled order and no alert.
                _flag_amount_mismatch(
                    order, transaction_id, verified_amount, expected_amount
                )
                return VivaWebhookEvent.OUTCOME_SKIPPED
        except TypeError, ValueError, AttributeError, InvalidOperation:
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
        return VivaWebhookEvent.OUTCOME_SKIPPED

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

    dispatch_on_commit(send_order_confirmation_email, [order.id])

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

    # Verify FIRST, settled-guard second. The multi-tenant candidate loop
    # rests on one invariant: a tenant that does not own the transaction
    # cannot answer anything but 500, because Retrieve-Transaction fails
    # against its credentials. Returning early — for any reason — before
    # verification breaks that. A merchant can plant another tenant's
    # orderCode in their own order's metadata (which is why candidates are
    # a LIST), so an already-settled order on the wrong tenant would
    # short-circuit here, write the audit row into that tenant's schema,
    # and hand Viva a 200. The real owner never sees the event and Viva
    # does not redeliver after a 200.
    if not _verify_viva_terminal_transaction(
        f"order {order.id}",
        transaction_id,
        {PaymentStatus.FAILED, PaymentStatus.CANCELED},
        "payment_failed",
        order=order,
    ):
        return

    # Guard: a stale or out-of-order "payment failed" Viva event must not
    # overwrite a financially settled state.  Viva does NOT guarantee
    # delivery order.
    if order.payment_status in SETTLED_PAYMENT_STATUSES:
        logger.warning(
            "Ignoring stale payment_failed (Viva) for order %s: "
            "payment_status already %s",
            order.id,
            order.payment_status,
        )
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

    dispatch_on_commit(send_payment_failed_email, [order.id])


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
        f"order {order.id}",
        transaction_id,
        {PaymentStatus.REFUNDED, PaymentStatus.PARTIALLY_REFUNDED},
        "reversal",
        order=order,
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
