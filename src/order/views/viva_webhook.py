import hashlib
import ipaddress
import json
import logging
from base64 import b64encode
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from tenant.credentials import VivaWalletCredentials

import requests
from django.core.cache import cache
from django.db import connection, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils.translation import gettext
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_tenants.utils import (
    schema_context,
    tenant_context,
)

from core.cache_backend import REDIS_UNAVAILABLE
from core.client_ip import client_ip_or_peer
from order.enum.status import (
    SETTLED_PAYMENT_STATUSES,
    OrderStatus,
    PaymentStatus,
)
from order.models.history import OrderHistory
from order.models.order import Order
from order.payments import payment_hold, viva_settlement
from order.payments.disputes import announce_chargeback
from order.payments.payment import order_viva_codes, provider_amount
from tenant.celery import dispatch_on_commit


def _resolve_tenant_candidates(order_code: str) -> list:
    """Every active, non-suspended tenant whose Order matches order_code.

    Viva webhooks land in the public schema (no tenant routing exists at
    the HTTP layer for machine-to-machine callers), so ownership is
    found by iterating tenants and looking the order up via
    ``viva_order_code_q`` — matching any code in the
    ``viva_order_codes`` history array, because every
    ``create_checkout_session`` mints a fresh code and a shopper on a
    stale tab may pay an earlier one.

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

    candidates = []
    for tenant in Tenant.objects.active():
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

    for tenant in Tenant.objects.unavailable():
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


def _webhook_get_rate_limit(request, *, answer_is_free: bool) -> bool:
    """Return True when the request should be blocked.

    Applies a 10-req/hour per-IP cap on the GET verification endpoint.

    With Redis unreachable it fails OPEN only while ``answer_is_free``:
    the store has its verification key stored, so answering costs one
    credentials read and nothing else, and a Redis outage must not take
    down Viva's handshake. Without a stored key every answer fetches it
    from Viva with the store's credentials, so an unthrottled caller would
    spend the store's Viva budget: the cache's error goes on, and the
    request is refused with 503 (``core.middleware.strict_cache``, the rule
    beside ``core.cache_backend.STRICT_KEY_PREFIXES``). Only the cache's
    unavailability is caught; any other error is a bug.

    Keyed on ``client_ip_or_peer``, never a bare ``X-Real-IP``: a caller
    that chooses that header gets a fresh budget per request.

    Deliberately NOT an IP allowlist, though Viva publishes its ranges
    (VIVA_WEBHOOK_IPS_PRODUCTION / _DEMO): that would fail CLOSED on the
    handshake, and a range changing on Viva's side would silently break
    webhook registration for every tenant. The rate limit degrades
    instead. Tighten this only together with a way to notice that
    breakage.
    """
    ip = client_ip_or_peer(request)
    key = "viva_wh_get:" + hashlib.sha256(ip.encode()).hexdigest()[:24]
    try:
        return cache.count_in_window(key, 3600) > 10
    except REDIS_UNAVAILABLE:
        if not answer_is_free:
            raise
        logger.warning("viva_webhook GET rate-limit: cache error, failing open")
        return False


def _handle_verification(request):
    from tenant.credentials import viva_wallet_credentials

    creds = viva_wallet_credentials()
    verification_key = creds["webhook_verification_key"]

    if _webhook_get_rate_limit(request, answer_is_free=bool(verification_key)):
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

    logger.info("Returning Viva verification key")
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
    """Whether the webhook's source address is in Viva's published range.

    Returns (is_viva_ip, observed_ip), for the log line only: nothing
    is skipped or refused on it. The Retrieve Transaction call in
    ``_process_event_in_tenant`` is the authentication — it needs our
    own OAuth2 credentials and confirms the transaction exists at Viva.

    The address is ``client_ip_or_peer``, the one a caller cannot
    choose. Scanning every ``X-Forwarded-For`` entry for a match would
    report whatever Viva address a caller wrote into the header. It
    stays informational because refusing on it would fail closed the
    day Viva's hardcoded ranges change.

    Caller must already be inside the resolved tenant's
    ``schema_context`` so ``live_mode`` reflects THIS tenant's Viva
    account — there is no platform-wide live/demo mode setting.
    """
    from tenant.credentials import viva_wallet_credentials

    live_mode = viva_wallet_credentials()["live_mode"]
    allowed_networks = (
        VIVA_WEBHOOK_IPS_PRODUCTION if live_mode else VIVA_WEBHOOK_IPS_DEMO
    )

    observed = client_ip_or_peer(request)
    try:
        source = ipaddress.ip_address(observed)
    except ValueError:
        return False, observed
    return any(source in network for network in allowed_networks), observed


def _verify_viva_terminal_transaction(
    subject,
    transaction_id,
    expected_statuses,
    event_label,
    *,
    order=None,
    reported=None,
):
    """Verify a failed (1798) Viva event against the Retrieve Transaction
    API before mutating financial state (G0275).

    The webhook endpoint is unauthenticated — there is no HMAC and the
    source-IP check is non-blocking — so the event body must not be trusted
    to flip financial state: a spoofed 1798 could mark an order or a
    gift-card purchase FAILED. Mirroring the 1796 path, we confirm with
    Viva that the transaction genuinely reached the expected terminal
    state. A reversal (1797) is verified by :func:`_verify_viva_reversal`
    instead: its transaction is a refund of its own, not the payment
    reaching a terminal status.

    *subject* is a human label for the thing being mutated ("order 42",
    "gift-card purchase <uuid>") and is used only in the log lines, so this
    guard can be shared by callers that have no order model.

    *order*, when given, additionally requires the verified transaction to
    carry one of THAT order's own Viva order codes. Viva's instruction is
    to confirm a result by the COMBINATION of OrderCode and TransactionId;
    a caller with no order passes nothing and gets the status/amount half
    only.

    *reported* is Retrieve Transaction's answer when the caller read it
    before locking (:func:`_read_event_transaction`).

    Returns the VERIFIED status to proceed with (truthy). Returns ``None``
    (skip, no mutation) when the event carries no ``TransactionId`` or the
    verified status is not one we expect. Raises ``RuntimeError`` (→ 500,
    Viva retries) when verification is UNAVAILABLE — see
    :func:`viva_settlement.retrieve_transaction` — so an unverifiable event can never
    mutate state on a trusted-by-default basis.
    """
    if not transaction_id:
        logger.error(
            "Viva %s event for %s carries no TransactionId — refusing "
            "to mutate payment state without verification",
            event_label,
            subject,
        )
        return None

    verified_status, verified_data = (
        reported
        if reported is not None
        else viva_settlement.retrieve_transaction(transaction_id)
    )

    # Viva's own instruction is to confirm the result with the
    # COMBINATION of OrderCode and TransactionId
    # (developer.viva.com/webhooks-for-payments/transaction-payment-created),
    # then check StatusId and Amount. Without the first half, anyone can
    # post their own real TransactionId against someone else's OrderCode.
    if order is not None and not viva_settlement.transaction_belongs_to_order(
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
        return None

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
        return None

    return verified_status


def _verify_viva_reversal(
    subject, transaction_id, order_codes, type_ids, *, reported=None
) -> tuple[Decimal, int] | None:
    """Verify a reversal (1797) of one of *type_ids* and return how much it
    moved and its ``TransactionTypeId``.

    The event's ``TransactionId`` is the REFUND, a transaction of its own
    (``ParentId`` names the payment it reverses). Per Viva's documented
    shape (developer.viva.com/webhooks-for-payments/transaction-reversal-created,
    developer.viva.com/integration-reference/response-codes) that
    transaction has ``StatusId`` ``F`` — ``R`` is the status of the
    ORIGINAL payment — a refund or void ``TransactionTypeId``, and a
    negative amount, because "transactions which reverse previous
    payments will always have a negative amount". Waiting for a
    ``REFUNDED`` status on it is what dropped every 1797 for good.

    Everything is read from Retrieve Transaction, never from the
    unauthenticated body: a spoofed event must not be able to refund an
    order or void a gift card. The transaction must also carry one of
    *order_codes* (OrderCode AND TransactionId, Viva's own instruction),
    or anyone could post their OWN real refund against someone else's
    order.

    Returns the amount as a positive ``Decimal`` with the type id, or ``None``
    (skip, no mutation) for an event that is not a verified reversal of one
    of *order_codes* of a wanted type. Raises ``RuntimeError`` (→ 500, Viva retries) when
    verification is UNAVAILABLE, including a response with no readable
    amount.
    """
    from order.payments.payment import VIVA_STATUS_FINISHED

    if not transaction_id:
        logger.error(
            "Viva reversal event for %s carries no TransactionId — "
            "refusing to mutate payment state without verification",
            subject,
        )
        return None

    _status, data = (
        reported
        if reported is not None
        else viva_settlement.retrieve_transaction(transaction_id)
    )

    if not viva_settlement.transaction_belongs_to_codes(
        order_codes, data, subject
    ):
        logger.error(
            "Viva reversal event: transaction %s reports order_code %r, "
            "which is not one of %s's issued codes — refusing to mutate "
            "state",
            transaction_id,
            data.get("order_code"),
            subject,
        )
        return None

    status_id = data.get("raw_status")
    type_id = data.get("transaction_type_id")
    if status_id != VIVA_STATUS_FINISHED or type_id not in type_ids:
        logger.warning(
            "Viva reversal event for %s: transaction %s is not a completed "
            "refund (statusId=%r, transactionTypeId=%r) — skipping",
            subject,
            transaction_id,
            status_id,
            type_id,
        )
        return None

    signed_amount = provider_amount(data.get("amount"))
    if signed_amount is None:
        raise RuntimeError(
            f"Viva refund transaction {transaction_id} carries no readable "
            "amount"
        )
    if signed_amount >= 0:
        logger.warning(
            "Viva reversal event for %s: transaction %s has a non-negative "
            "amount, so it reverses nothing — skipping",
            subject,
            transaction_id,
        )
        return None

    return -signed_amount, type_id


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
    # GDPR Art. 32. The transaction is named by its hash here
    # (``transaction_hash`` says why that is a name, not a secret).
    txn_hash = viva_settlement.transaction_hash(transaction_id)
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

    if event_type_id == VIVA_ACCOUNT_TRANSACTION_CREATED:
        return _handle_account_transaction_event(request, event_data)

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
    return _process_in_first_verifying_tenant(
        candidates,
        request,
        subject=f"order code {order_code}",
        process=lambda: _process_event_in_tenant(
            order_code=order_code,
            event_type_id=event_type_id,
            event_data=event_data,
            transaction_id=transaction_id,
            status_id=status_id,
            txn_hash=txn_hash,
        ),
    )


def _process_in_first_verifying_tenant(
    candidates, request, *, subject, process
):
    """Run *process* inside each candidate tenant until one does not answer
    500, and return that response (the last 500 when none does).

    The verify-then-select contract of the order-code resolution above, shared
    with the dispute path: a candidate that does not own the transaction fails
    its Retrieve Transaction call, so ``process`` answers 500 with its atomic
    block rolled back (no side effects) and the next candidate is tried.
    *subject* names what matched, for the log lines.
    """
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
                    "Viva webhook from non-Viva IP %s — authenticated by "
                    "the transaction API as always",
                    observed_ip,
                )

            response = process()

        if response.status_code != 500:
            if multi:
                logger.warning(
                    "Viva webhook: %s matched %d tenants; "
                    "processed in the one that verified (schema=%s)",
                    subject,
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
                "the transaction for %s — trying next candidate",
                tenant.schema_name,
                subject,
            )

    if multi:
        logger.error(
            "Viva webhook: %s matched %d tenants but NONE "
            "verified the transaction — returning 500 so Viva retries",
            subject,
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

    # Idempotency: the ``VivaWebhookEvent`` row of ``(transaction_id,
    # event_type_id)`` is the single source of truth, the same one a
    # payment read back from Viva writes (``viva_settlement``). The
    # unique constraint blocks replays at the DB level — admin metadata
    # edits cannot reopen the door.
    handle, read = _EVENT_HANDLERS.get(event_type_id, (None, None))
    try:
        viva_settlement.apply_event(
            order,
            event_type_id=event_type_id,
            event_data=event_data,
            transaction_id=transaction_id,
            order_code=str(order_code or ""),
            status_id=str(status_id or ""),
            handle=handle,
            read=read,
        )
    except RuntimeError as exc:
        # Raised by a handler when Viva's verification API is
        # unreachable. Returning 500 signals Viva to retry the webhook;
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
    from giftcard.enum import GiftCardPurchaseStatus
    from giftcard.services import GiftCardService
    from order.models.viva_webhook_event import VivaWebhookEvent
    from order.payments.payment import (
        VIVA_REFUND_CLAIMED_TRANSACTION_TYPE_IDS,
        VIVA_REFUND_TRANSACTION_TYPE_IDS,
    )

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
        # Retrieve Transaction is an HTTP call: it is answered before the
        # purchase row is locked, never under it. The branch below judges
        # the answer under the lock.
        reported = _read_gift_card_event(
            purchase, event_type_id, event_data, transaction_id
        )
        with transaction.atomic():
            purchase = (
                type(purchase).objects.select_for_update().get(pk=purchase.pk)
            )

            # The read above judged the event from an unlocked purchase. A
            # purchase that moved since needs an answer it does not have:
            # refuse (500, Viva redelivers and the next delivery reads
            # first) rather than send a request under the lock.
            if (
                reported is None
                and transaction_id
                and _gift_card_event_needs_viva(
                    purchase, event_type_id, event_data
                )
            ):
                raise RuntimeError(
                    "Gift-card purchase changed between the Viva read and "
                    f"the lock for txn {transaction_id}"
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
                    verified_status, verified_data = reported
                    problem = _gift_card_payment_problem(
                        purchase, verified_data
                    )
                    if problem:
                        logger.error(
                            "Viva gift-card txn %s is not purchase %s's "
                            "payment: %s — refusing to complete",
                            transaction_id,
                            purchase.uuid,
                            problem,
                        )
                        outcome = VivaWebhookEvent.OUTCOME_SKIPPED
                        verified_status = None
                    if verified_status == PaymentStatus.COMPLETED:
                        GiftCardService.complete_purchase(
                            purchase, transaction_id=str(transaction_id)
                        )
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
                        reported=reported,
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
                # this must never run on the event's say-so: the refund has
                # to be verified AND carry this purchase's own order code.
                subject = f"gift-card purchase {purchase.uuid}"
                verified = _verify_viva_reversal(
                    subject,
                    transaction_id,
                    {str(purchase.payment_id)},
                    VIVA_REFUND_TRANSACTION_TYPE_IDS
                    | VIVA_REFUND_CLAIMED_TRANSACTION_TYPE_IDS,
                    reported=reported,
                )
                refunded = None if verified is None else verified[0]
                if verified is None:
                    outcome = VivaWebhookEvent.OUTCOME_SKIPPED
                elif verified[1] in VIVA_REFUND_CLAIMED_TRANSACTION_TYPE_IDS:
                    # A refund claim is the cardholder's bank taking the
                    # money back: the whole purchase, however much it
                    # names.
                    outcome = GiftCardService.handle_purchase_reversal(purchase)
                else:
                    # The card is the whole purchase: a goodwill refund of
                    # part of it must not void value the buyer still owns,
                    # so refunds add up and the card is voided once they
                    # reach the purchase amount. Each refund is counted
                    # once: its id is the idempotency key checked above,
                    # and the unique ``VivaWebhookEvent`` row written below
                    # in the same transaction rolls this increment back for
                    # a parallel duplicate that got past that check.
                    purchase.refunded_amount += refunded
                    purchase.save(update_fields=["refunded_amount"])
                    if purchase.refunded_amount < purchase.amount.amount:
                        logger.warning(
                            "Viva has refunded %s of %s for %s: the card "
                            "is voided once the refunds reach the whole "
                            "purchase",
                            purchase.refunded_amount,
                            purchase.amount,
                            subject,
                        )
                    else:
                        outcome = GiftCardService.handle_purchase_reversal(
                            purchase
                        )
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


def _gift_card_event_needs_viva(
    purchase, event_type_id, event_data: dict
) -> bool:
    """Whether the branch for *event_type_id* judges a Retrieve Transaction
    answer for *purchase* as it is now (the event carrying a transaction)."""
    from giftcard.enum import GiftCardPurchaseStatus

    if event_type_id == 1796:
        return event_data.get("StatusId", "") in {"", "F"}
    if event_type_id == 1798:
        return purchase.status == GiftCardPurchaseStatus.PENDING
    return event_type_id == 1797


def _read_gift_card_event(
    purchase, event_type_id, event_data: dict, transaction_id: str
):
    """What a gift-card purchase event reads from Viva before the purchase
    row is locked: Retrieve Transaction for the events that verify one
    (the answer the branch judges under the lock), or ``None`` when the
    branch will refuse the event without reading it.

    Raises ``RuntimeError`` when Viva cannot be read, as the branch did.
    """
    if not transaction_id or not _gift_card_event_needs_viva(
        purchase, event_type_id, event_data
    ):
        return None
    return viva_settlement.retrieve_transaction(transaction_id)


def _gift_card_payment_problem(purchase, verified_data) -> str | None:
    """Why the VERIFIED Viva transaction is not the payment of *purchase*;
    ``None`` when it is.

    The same three-way check the order path makes (``viva_settlement.handle_payment_created``):
    the transaction must carry THIS purchase's own order code, its currency
    must be the purchase's, and its amount must be the purchase amount.
    Currency and amount are required, not optional: a transaction that does
    not report them proves nothing about what was paid. Without the order
    code, anyone could post their own real transaction against another
    payment's order code and mint a card from it.

    An absent order code raises (see ``viva_settlement.transaction_belongs_to_codes``).
    """
    if not viva_settlement.transaction_belongs_to_codes(
        {str(purchase.payment_id)},
        verified_data,
        f"gift-card purchase {purchase.uuid}",
    ):
        return "it carries another order code"
    currency = verified_data.get("currency")
    if currency != purchase.amount.currency.code:
        return f"its currency is {currency!r}"
    amount = provider_amount(verified_data.get("amount"))
    if amount is None:
        return "it reports no readable amount"
    if abs(amount - purchase.amount.amount) > Decimal("0.01"):
        return f"its amount is {amount}, not {purchase.amount.amount}"
    return None


def _handle_transaction_price_calculated(
    order, event_data, transaction_id, *, reported=None
):
    """Record the commission Viva withdrew for a payment (event 1799).

    ``TotalCommission`` is the interchange fee, the ISV fee and Viva's own
    fee together, in major units of ``CurrencyCode``
    (developer.viva.com/webhooks-for-payments/transaction-price-calculated).
    It lands on the ledger row of the payment as its ``fee``, the provider's
    own figure, so the profit report can count what the payment cost.
    ``Interchange`` is not read: Viva's own samples disagree on its sign, and
    the total is all the report needs.

    Verified like every other event: the transaction is retrieved from Viva
    with this tenant's credentials and must carry one of the order's own
    order codes, so a forged body, or another store's transaction, records
    nothing (a ``RuntimeError`` answers 500, and the candidate loop moves on).
    Returns ``OUTCOME_SKIPPED`` when the report is unusable; ``None`` when the
    fee was written, or kept for the payment row that does not exist yet.
    """
    from djmoney.money import Money

    from order.enum.payment_transaction import PaymentFeeSource
    from order.models.viva_webhook_event import VivaWebhookEvent
    from order.payments.payment import VivaWalletPaymentProvider, alpha_currency
    from order.payments.payment_ledger import PaymentLedger

    if not transaction_id:
        logger.error(
            "Viva webhook event %s missing TransactionId for order %s: "
            "cannot verify, no fee recorded",
            VIVA_TRANSACTION_PRICE_CALCULATED,
            order.id,
        )
        return VivaWebhookEvent.OUTCOME_SKIPPED

    _status, verified_data = (
        reported
        if reported is not None
        else viva_settlement.retrieve_transaction(transaction_id)
    )
    if not viva_settlement.transaction_belongs_to_order(order, verified_data):
        logger.error(
            "Viva transaction %s is none of order %s's payments: no fee "
            "recorded",
            transaction_id,
            order.id,
        )
        return VivaWebhookEvent.OUTCOME_SKIPPED

    # The commission fields only, never the whole body: the allowlist the
    # webhook log keeps for every event (no merchant or reseller ids).
    reported = {
        name: event_data.get(name)
        for name in ("CurrencyCode", "TotalCommission", "Interchange", "IsvFee")
    }
    logger.info(
        "Viva transaction price calculated for order %s: %s", order.id, reported
    )
    currency = alpha_currency(event_data.get("CurrencyCode"))
    commission = provider_amount(event_data.get("TotalCommission"))
    if currency is None or commission is None or commission < 0:
        logger.error(
            "Viva transaction %s reports no usable commission for order %s: %s",
            transaction_id,
            order.id,
            reported,
        )
        return VivaWebhookEvent.OUTCOME_SKIPPED

    # A report that comes before the payment webhook has booked the charge
    # is kept by the ledger and written when that row is recorded: answering
    # it with a skip would dedup the event and lose the fee for good.
    PaymentLedger.record_fee(
        provider=VivaWalletPaymentProvider.code,
        reference=transaction_id,
        fee=Money(commission, currency),
        source=PaymentFeeSource.VIVA_TRANSACTION_PRICE,
    )
    return None


def _handle_payment_failed(order, event_data, transaction_id, *, reported=None):
    from order.models.viva_webhook_event import VivaWebhookEvent

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
        reported=reported,
    ):
        return VivaWebhookEvent.OUTCOME_SKIPPED

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
        return VivaWebhookEvent.OUTCOME_SKIPPED

    previous_payment_status = order.payment_status
    order.payment_status = PaymentStatus.FAILED
    order.save(update_fields=["payment_status"])

    OrderHistory.log_payment_update(
        order=order,
        previous_value={"payment_status": previous_payment_status},
        new_value={
            "payment_status": PaymentStatus.FAILED,
            "payment_id": transaction_id,
            "provider": "viva_wallet",
        },
    )

    from order.payments.payment_events import publish_payment_status

    publish_payment_status(order)


def _handle_reversal_created(
    order, event_data, transaction_id, *, reported=None, parent=None
):
    """Book a refund Viva made against one of this order's payments.

    Mirrors ``handle_stripe_refund_made``: the refund becomes a
    ``PaymentTransaction`` row, ``PaymentLedger.record_refund`` derives
    ``payment_status`` from the ledger's sums, and ``order_refunded`` fires
    so the refund email, live WS toast, and Meta CAPI Refund event all run.
    ``Order.status`` is left untouched — deciding whether a reversal also
    means the goods are returned is a business call the admin owns.

    The event's ``TransactionId`` is the refund and ``ParentId`` the
    payment it reverses (:func:`_verify_viva_reversal`). The ledger holds
    every payment of the order (it can be paid twice, under two of its
    checkout codes), so the refund is attached to the payment ``ParentId``
    names. REFUNDED releases the gift-card redemption and the loyalty
    points, which come back once, with the last refund.

    A refund the in-app path already booked reaches us again as a 1797 of
    its own (Viva notifies every refund, whoever made it), and Viva
    redelivers: both hit the ledger's unique constraint on the refund
    transaction id and change nothing.

    A reversal whose type is a refund claim (``VIVA_REFUND_CLAIMED_*``) is a
    chargeback and goes to :func:`_record_viva_chargeback` instead, under the
    same key as a lost dispute read from the payment's status, so one
    dispute is booked once however Viva reports it.

    Returns ``OUTCOME_SKIPPED`` when the event changed nothing, so the
    audit row says so; ``None`` when a refund was booked.
    """
    from djmoney.money import Money

    from order.models.viva_webhook_event import VivaWebhookEvent
    from order.payments.payment import (
        VIVA_REFUND_CLAIMED_TRANSACTION_TYPE_IDS,
        VIVA_REFUND_TRANSACTION_TYPE_IDS,
    )
    from order.payments.payment_ledger import PaymentLedger
    from order.refunds.service import RefundService
    from order.signals import order_refunded

    logger.info(
        "Viva Wallet reversal created for order %s",
        order.id,
    )

    # Verify FIRST, whatever else could end the event early: a tenant that
    # does not own the transaction must answer 500 so the candidate loop
    # moves on (see ``_handle_payment_failed``).
    verified = _verify_viva_reversal(
        f"order {order.id}",
        transaction_id,
        order_viva_codes(order),
        VIVA_REFUND_TRANSACTION_TYPE_IDS
        | VIVA_REFUND_CLAIMED_TRANSACTION_TYPE_IDS,
        reported=reported,
    )
    if verified is None:
        return VivaWebhookEvent.OUTCOME_SKIPPED
    refunded, type_id = verified

    # The reversal must reverse one of THIS order's payments, not another
    # payment that happens to share its order code. Retrieve Transaction
    # does not document a parent id, so this is the event's own
    # ``ParentId``; it can only refuse, never grant — the reversal itself was
    # verified above, and the payment it is attached to comes from our own
    # ledger rows, the order's own ``payment_id``, or Viva saying the parent
    # was paid on one of the order's codes (:func:`_viva_charge_for`).
    parent_id = str(event_data.get("ParentId") or "")
    charge = _viva_charge_for(order, parent_id, reported=parent)
    if charge is None:
        logger.error(
            "Viva reversal %s names parent %r, which is none of order %s's "
            "payments — skipping",
            transaction_id,
            parent_id,
            order.id,
        )
        # The parent could not be read or is none of this order's: the
        # event cannot be judged, and recording it would let it stand in
        # for Viva's own event for this refund.
        return viva_settlement.OUTCOME_NOT_RECORDED

    if type_id in VIVA_REFUND_CLAIMED_TRANSACTION_TYPE_IDS:
        # A refund claim is money the cardholder's bank took back: a
        # chargeback, not a refund the merchant made.
        return _record_viva_chargeback(
            order, charge, Money(refunded, charge.amount.currency)
        )

    previous_payment_status = order.payment_status
    refund = Money(refunded, charge.amount.currency)
    refund_row, created = RefundService.book_refund(
        order, charge=charge, amount=refund, reference=transaction_id
    )
    if not created:
        logger.info(
            "Viva reversal %s is already recorded on order %s — skipping",
            transaction_id,
            order.id,
        )
        return VivaWebhookEvent.OUTCOME_SKIPPED

    OrderHistory.log_payment_update(
        order=order,
        previous_value={
            "payment_status": previous_payment_status,
        },
        new_value={
            "payment_status": order.payment_status,
            "refund_id": transaction_id,
            "amount": str(refunded),
            "provider": "viva_wallet",
        },
    )

    order_refunded.send(
        sender=Order, order=order, amount=refund, refund=refund_row
    )

    # An order never confirmed must not stay PENDING with a refunded
    # payment unnoticed: no sweep selects a settled payment. One the
    # refund leaves holding none of the customer's money is canceled, its
    # stock released and anything paid by gift card or points given back.
    # One still holding part is flagged for the store as a charge that
    # does not match the order.
    # An order waiting for the store's review is the store's to close: the
    # refund is booked and the order stays on the worklist (a charge for the
    # wrong amount is never booked, so the ledger cannot say what is still
    # at Viva).
    if (
        order.status != OrderStatus.PENDING
        or payment_hold.awaits_payment_review(order)
    ):
        return None
    held = PaymentLedger.held(order)
    if held <= 0:
        from order.lifecycle.service import OrderLifecycleService

        OrderLifecycleService.cancel_order(
            order,
            reason="Canceled: its payment was refunded before it was confirmed",
            refund_payment=False,
            customer_initiated=False,
        )
    else:
        from order.payments.payment import VivaWalletPaymentProvider
        from order.services import OrderService

        OrderService.flag_payment_mismatch(
            order,
            payment_id=charge.provider_reference,
            provider=VivaWalletPaymentProvider.code,
            observed=Money(held, charge.amount.currency),
            expected=order.calculate_order_total_amount(),
        )
    return None


def _viva_charge_for(order, payment_id: str, reported=None):
    """The ledger's CHARGE of *order* for Viva transaction *payment_id*, or
    ``None`` when it is none of the order's payments.

    A payment of the order the ledger has not recorded is read from Viva
    once and recorded: the order's own ``payment_id`` (an order paid while
    the previous release was serving), or any transaction Viva says was
    paid on one of the order's codes. The second is a payment no webhook
    has booked yet (its 1796 is late, lost, or verified and waiting for
    the order's lock): a refund or dispute of it is booked against it;
    one Viva reports uncaptured is none. *reported* is Retrieve
    Transaction's answer for *payment_id*, read before the order was
    locked; nothing is read here. Raises ``RuntimeError`` (→ 500, Viva
    retries) when the order's own payment has no such answer, or Viva
    reports it uncaptured: retry rather than guess.
    """
    from order.payments.payment import VivaWalletPaymentProvider
    from order.payments.payment_ledger import PaymentLedger

    if not payment_id:
        return None
    charge = viva_settlement.ledger_charge(order, payment_id)
    if charge is not None:
        return charge
    owned = bool(order.payment_id) and (
        payment_id.lower() == order.payment_id.lower()
    )
    if owned:
        payment_id = order.payment_id
        if reported is None:
            # Read before the lock when it is needed; never under it.
            raise RuntimeError(
                f"Viva payment {payment_id} was not read before the lock"
            )
    else:
        if reported is None or not viva_settlement.transaction_belongs_to_order(
            order, reported[1]
        ):
            return None
    charge = PaymentLedger.ensure_charge(
        order,
        provider_code=VivaWalletPaymentProvider.code,
        payment_id=payment_id,
        reported=reported,
    )
    if charge is None and owned:
        # The order's own payment could not be confirmed captured: retry.
        # Another transaction Viva reports uncaptured is no payment of it.
        raise RuntimeError(
            f"Viva captured amount unavailable for payment {payment_id}"
        )
    return charge


def _record_viva_chargeback(order, charge, amount):
    """Book a Viva chargeback of *amount* against *charge*, with the red
    history entry and the staff email. No customer email and no
    ``order_refunded``: nothing was paid back.

    Keyed on the CHARGE's own Viva transaction id, the original sale. The
    ledger's key includes the kind, so it cannot collide with the CHARGE row,
    and a refund claim (1797) and a lost dispute (2054) for the same sale hit
    the same key and book it once.

    Viva names only the whole sale, so *amount* is capped by the ledger at
    what the payment still holds (a sale partly refunded before the dispute).
    When nothing is left no row is written, but staff are still told, once:
    ``dispute_resolved_id`` remembers it, because every wallet movement of
    the dispute arrives as an event of its own.

    Returns ``OUTCOME_SKIPPED`` when it was already recorded or announced,
    ``None`` when it was booked or announced now.
    """
    from order.models.viva_webhook_event import VivaWebhookEvent
    from order.payments.payment import VivaWalletPaymentProvider
    from order.payments.payment_ledger import PaymentLedger

    reference = charge.provider_reference
    if (order.metadata or {}).get("dispute_resolved_id") == reference:
        return VivaWebhookEvent.OUTCOME_SKIPPED
    outcome = PaymentLedger.record_chargeback(
        order, charge=charge, amount=amount, reference=reference
    )
    if outcome.row is None:
        metadata = dict(order.metadata or {})
        metadata.pop("disputed", None)
        metadata["dispute_resolved_id"] = reference
        order.metadata = metadata
        order.save(update_fields=["metadata"])
    if not announce_chargeback(
        order,
        outcome,
        dispute_id=reference,
        provider_code=VivaWalletPaymentProvider.code,
    ):
        logger.info(
            "Viva chargeback for sale %s is already recorded on order %s — "
            "skipping",
            reference,
            order.id,
        )
        return VivaWebhookEvent.OUTCOME_SKIPPED
    return None


#: Viva ``EventTypeId`` of Transaction Price Calculated: the commission Viva
#: withdrew for a payment.
VIVA_TRANSACTION_PRICE_CALCULATED = 1799

#: Viva ``EventTypeId`` of Account Transaction Created
#: (developer.viva.com/webhooks-for-payments/account-transaction-created/):
#: fires on any wallet balance change, disputes and chargebacks among them,
#: and also on clearances, fees and payouts. Its payload carries no
#: ``OrderCode``; ``EventData.SaleTransactionId`` is "the transaction id
#: which is matched to the wallet transaction".
VIVA_ACCOUNT_TRANSACTION_CREATED = 2054


def _tenants_holding_sale(tenants, sale_id: str) -> list:
    """The *tenants* whose ledger holds a Viva CHARGE for *sale_id*, or
    whose order was paid by that transaction."""
    from giftcard.models import GiftCardPurchase
    from order.enum.payment_transaction import PaymentTransactionKind
    from order.models.payment_transaction import PaymentTransaction
    from order.payments.payment import VivaWalletPaymentProvider

    holders = []
    for tenant in tenants:
        try:
            with schema_context(tenant.schema_name):
                if (
                    PaymentTransaction.objects.filter(
                        kind=PaymentTransactionKind.CHARGE,
                        provider=VivaWalletPaymentProvider.code,
                        provider_reference__iexact=sale_id,
                    ).exists()
                    or Order.objects.filter(payment_id__iexact=sale_id).exists()
                    or GiftCardPurchase.objects.filter(
                        provider_code=VivaWalletPaymentProvider.code,
                        provider_transaction_id__iexact=sale_id,
                    ).exists()
                ):
                    holders.append(tenant)
        except Exception:
            # A destroyed tenant's schema may be gone already (see
            # ``_order_exists_on_unavailable_tenant``).
            logger.warning(
                "Viva webhook: skipped tenant %s while resolving a sale — "
                "its schema may already be gone",
                getattr(tenant, "schema_name", tenant),
                exc_info=True,
            )
    return holders


def _sale_exists_on_unavailable_tenant(sale_id: str) -> bool:
    """True when the sale is a payment of a suspended or inactive tenant:
    "come back later", not "this means nothing here"."""
    from tenant.models import Tenant

    return bool(_tenants_holding_sale(Tenant.objects.unavailable(), sale_id))


def _resolve_tenant_candidates_for_sale(sale_id: str) -> list:
    """Every active tenant whose ledger holds a Viva CHARGE for *sale_id*, or
    whose order was paid by that transaction.

    An account transaction has no order code to resolve a tenant by, and
    trying every Viva-configured tenant's credentials against every wallet
    movement (fees, payouts, clearances) would cost one Viva API call per
    tenant per event. The sale id is what a dispute is about, and ours is on
    the ledger, so this is database lookups only; the Retrieve Transaction
    call that follows is still what authenticates the event.
    """
    from tenant.models import Tenant

    return _tenants_holding_sale(Tenant.objects.active(), sale_id)


def _handle_account_transaction_event(request, event_data: dict):
    """Account Transaction Created (2054): follow a dispute of one of our
    payments.

    Everything else this event reports (clearances, fees, payouts, a sale
    that is none of ours, an event without ``SaleTransactionId``) is
    acknowledged and ignored. SubTypeIds are not interpreted: Viva
    documents no subtype for the chargeback debit itself, so the dispute is
    read from the original payment's ``StatusId`` (``M``/``MA``/``MI`` open,
    ``ML`` lost, ``MW`` won), retrieved from Viva, never from the body.
    """
    sale_id = str(event_data.get("SaleTransactionId") or "")
    if not sale_id:
        logger.info("Viva account transaction without SaleTransactionId")
        return JsonResponse({"status": "ok"})

    candidates = _resolve_tenant_candidates_for_sale(sale_id)
    if not candidates and _sale_exists_on_unavailable_tenant(sale_id):
        # Same as the payment path: Viva redelivers on a non-2xx (hourly,
        # 24 attempts), and a 200 would lose the dispute for good.
        logger.error(
            "Viva account transaction for a sale of a suspended or inactive "
            "tenant — refusing to acknowledge so Viva redelivers. "
            "Reactivate the tenant within 24h or the dispute is lost."
        )
        return JsonResponse({"status": "tenant_unavailable"}, status=503)
    if not candidates:
        logger.info(
            "Viva account transaction for a sale that is no store's payment "
            "— ignoring"
        )
        return JsonResponse({"status": "ok"})

    transaction_id = str(event_data.get("TransactionId") or "")
    return _process_in_first_verifying_tenant(
        candidates,
        request,
        subject="a dispute sale transaction",
        process=lambda: _process_dispute_event_in_tenant(
            sale_id=sale_id, transaction_id=transaction_id
        ),
    )


def _process_dispute_event_in_tenant(
    *, sale_id: str, transaction_id: str
) -> JsonResponse:
    """The dispute state of payment *sale_id* inside an active tenant schema.

    Same contract as ``_process_event_in_tenant``: the event is deduplicated
    on ``VivaWebhookEvent`` (its own ``TransactionId`` and 2054), a failed
    verification answers 500 with nothing written so the candidate loop moves
    on, and the audit row is written last.
    """
    from djmoney.money import Money

    from order.models.viva_webhook_event import VivaWebhookEvent
    from order.payments.payment import (
        VIVA_DISPUTE_LOST_STATUS_ID,
        VIVA_DISPUTE_OPEN_STATUS_IDS,
        VIVA_DISPUTE_WON_STATUS_ID,
    )

    def processed() -> bool:
        return bool(
            transaction_id
            and VivaWebhookEvent.objects.filter(
                transaction_id=transaction_id,
                event_type_id=VIVA_ACCOUNT_TRANSACTION_CREATED,
            ).exists()
        )

    if processed():
        return JsonResponse({"status": "ok"})

    try:
        with transaction.atomic():
            # Authenticate and match first: Retrieve Transaction names the
            # order code, and OrderCode AND TransactionId together are what
            # prove the sale is this order's.
            sale = viva_settlement.retrieve_transaction(sale_id)
            data = sale[1]
            order_code = data.get("order_code")
            if not order_code:
                raise RuntimeError(
                    f"Viva transaction {sale_id!r} was verified without an "
                    "orderCode — cannot match it to an order"
                )
            order = (
                Order.objects.select_for_update()
                .filter(viva_order_code_q(order_code))
                .first()
            )
            charge = (
                _viva_charge_for(order, sale_id, reported=sale)
                if order
                else None
            )
            if order is None or charge is None:
                purchase = _viva_gift_card_purchase_for(order_code, sale_id)
                if purchase is not None:
                    outcome = _follow_gift_card_dispute(
                        purchase, data.get("raw_status") or "", sale_id
                    )
                    if transaction_id and not processed():
                        VivaWebhookEvent.objects.create(
                            transaction_id=transaction_id,
                            event_type_id=VIVA_ACCOUNT_TRANSACTION_CREATED,
                            order=None,
                            order_code=str(order_code),
                            status_id=(data.get("raw_status") or "")[:4],
                            outcome=outcome,
                        )
                    return JsonResponse({"status": "ok"})
                logger.info(
                    "Viva dispute event: sale %s matches no order payment — "
                    "ignoring",
                    sale_id,
                )
                return JsonResponse({"status": "ok"})
            if processed():
                return JsonResponse({"status": "ok"})

            raw_status = data.get("raw_status") or ""
            outcome = VivaWebhookEvent.OUTCOME_SKIPPED
            if raw_status in VIVA_DISPUTE_OPEN_STATUS_IDS:
                outcome = _note_viva_dispute_opened(
                    order, charge.provider_reference, raw_status
                )
            elif raw_status == VIVA_DISPUTE_LOST_STATUS_ID:
                reported = provider_amount(data.get("amount"))
                if reported is None:
                    raise RuntimeError(
                        f"Viva transaction {sale_id} carries no readable amount"
                    )
                claimed = abs(reported)
                outcome = _record_viva_chargeback(
                    order,
                    charge,
                    Money(
                        claimed,
                        data.get("currency") or charge.amount.currency,
                    ),
                )
            elif raw_status == VIVA_DISPUTE_WON_STATUS_ID:
                outcome = _note_viva_dispute_won(
                    order, charge.provider_reference
                )
            else:
                logger.info(
                    "Viva dispute event: sale %s has status %r, which is no "
                    "dispute state — ignoring",
                    sale_id,
                    raw_status,
                )

            if transaction_id:
                VivaWebhookEvent.objects.create(
                    transaction_id=transaction_id,
                    event_type_id=VIVA_ACCOUNT_TRANSACTION_CREATED,
                    order=order,
                    order_code=str(order_code),
                    status_id=raw_status[:4],
                    outcome=outcome or VivaWebhookEvent.OUTCOME_PROCESSED,
                )
    except RuntimeError as exc:
        logger.error("Viva dispute event processing error: %s", exc)
        return JsonResponse(
            {"error": "Internal verification error, please retry"},
            status=500,
        )
    return JsonResponse({"status": "ok"})


def _viva_gift_card_purchase_for(order_code, sale_id: str):
    """The gift-card purchase paid by Viva sale *sale_id* under *order_code*
    (locked), or ``None`` when it is none of this store's purchases."""
    from giftcard.models import GiftCardPurchase

    return (
        GiftCardPurchase.objects.select_for_update()
        .filter(
            provider_code="viva_wallet",
            payment_id=str(order_code),
            provider_transaction_id__iexact=sale_id,
        )
        .first()
    )


def _follow_gift_card_dispute(purchase, raw_status: str, sale_id: str) -> str:
    """What a dispute state of a gift-card purchase's sale does to the card.

    Opened: nothing is voided yet (the money is still the merchant's to
    defend), staff are told. Lost: the payment is gone, so the card is
    voided and what was spent is recorded. Won, or any other status: nothing.
    Returns the ``VivaWebhookEvent`` outcome.
    """
    from giftcard.services import GiftCardService
    from order.models.viva_webhook_event import VivaWebhookEvent
    from order.payments.payment import (
        VIVA_DISPUTE_LOST_STATUS_ID,
        VIVA_DISPUTE_OPEN_STATUS_IDS,
    )

    if raw_status in VIVA_DISPUTE_OPEN_STATUS_IDS:
        told = GiftCardService.alert_purchase_disputed(purchase, sale_id)
        return (
            VivaWebhookEvent.OUTCOME_PROCESSED
            if told
            else VivaWebhookEvent.OUTCOME_SKIPPED
        )
    if raw_status == VIVA_DISPUTE_LOST_STATUS_ID:
        return GiftCardService.handle_purchase_reversal(purchase)
    return VivaWebhookEvent.OUTCOME_SKIPPED


def _note_viva_dispute_opened(order, dispute_id: str, raw_status: str):
    """Flag *order* as disputed and tell staff, once per dispute. The flag
    is what refuses a refund while the dispute is open
    (``PaymentLedger.dispute_open``)."""
    from order.models.viva_webhook_event import VivaWebhookEvent
    from order.payments.payment import VivaWalletPaymentProvider
    from order.tasks import send_dispute_notification_email

    metadata = dict(order.metadata or {})
    announced = (
        metadata.get("disputed") and metadata.get("dispute_id") == dispute_id
    )
    metadata.update(
        disputed=True, dispute_id=dispute_id, dispute_status=raw_status
    )
    order.metadata = metadata
    order.save(update_fields=["metadata"])
    if announced:
        return VivaWebhookEvent.OUTCOME_SKIPPED
    OrderHistory.log_note(
        order=order,
        note=gettext(
            "Viva Wallet dispute opened: sale transaction=%(dispute_id)s, "
            "status=%(status)s"
        )
        % {"dispute_id": dispute_id, "status": raw_status},
    )
    dispatch_on_commit(
        send_dispute_notification_email,
        [order.id, dispute_id, VivaWalletPaymentProvider.code],
    )
    return None


def _note_viva_dispute_won(order, dispute_id: str):
    """Clear the dispute flag and note it, once per dispute."""
    from order.models.viva_webhook_event import VivaWebhookEvent

    metadata = dict(order.metadata or {})
    if metadata.get("dispute_resolved_id") == dispute_id:
        return VivaWebhookEvent.OUTCOME_SKIPPED
    metadata.pop("disputed", None)
    metadata["dispute_resolved_id"] = dispute_id
    order.metadata = metadata
    order.save(update_fields=["metadata"])
    OrderHistory.log_note(
        order=order,
        note=gettext("Viva Wallet dispute won: sale transaction=%(dispute_id)s")
        % {"dispute_id": dispute_id},
    )
    return None


def _read_event_transaction(order, event_data, transaction_id):
    """:data:`viva_settlement.EventRead` of an event whose handler verifies
    its own transaction (1798, 1799): Retrieve Transaction, before the
    lock. An event naming none reads nothing; its handler refuses it."""
    if not transaction_id:
        return {}
    return {"reported": viva_settlement.retrieve_transaction(transaction_id)}


def _read_reversal(order, event_data, transaction_id):
    """:data:`viva_settlement.EventRead` of a 1797: the refund, and the
    payment its ``ParentId`` names when the ledger does not hold it yet
    (:func:`_viva_charge_for` books it from this answer).

    ``ParentId`` is the unauthenticated body's, so it is sent to Viva only
    once the refund itself is verified as one of this order's
    (:func:`_verify_viva_reversal` on the answer just read) and it has the
    shape of a Viva transaction id (a GUID). A parent Viva does not know is
    a payment of nobody's: it is not read, and the handler skips the event
    rather than answer 500 for a retry that cannot succeed."""
    from order.exceptions import (
        PaymentStatusUnavailableError,
        ProviderTransactionUnknownError,
    )
    from order.payments.payment import (
        VIVA_REFUND_CLAIMED_TRANSACTION_TYPE_IDS,
        VIVA_REFUND_TRANSACTION_TYPE_IDS,
        VivaWalletPaymentProvider,
    )

    read = _read_event_transaction(order, event_data, transaction_id)
    parent_id = str(event_data.get("ParentId") or "")
    if not (
        read
        and _is_viva_transaction_id(parent_id)
        and not viva_settlement.ledger_charge(order, parent_id)
        and _verify_viva_reversal(
            f"order {order.id}",
            transaction_id,
            order_viva_codes(order),
            VIVA_REFUND_TRANSACTION_TYPE_IDS
            | VIVA_REFUND_CLAIMED_TRANSACTION_TYPE_IDS,
            reported=read["reported"],
        )
    ):
        return read
    try:
        read["parent"] = VivaWalletPaymentProvider().get_payment_status(
            parent_id
        )
    except ProviderTransactionUnknownError:
        logger.warning(
            "Viva reversal of order %s names parent %r, which Viva does not "
            "know: skipping",
            order.id,
            parent_id,
        )
    except PaymentStatusUnavailableError as e:
        raise RuntimeError(
            f"Viva payment {parent_id} could not be read for a reversal"
        ) from e
    return read


def _is_viva_transaction_id(value: str) -> bool:
    """Whether *value* has the shape of a Viva transaction id, a GUID
    (Retrieve Transaction: ``transactionId``, ``string <uuid>``)."""
    try:
        UUID(value)
    except ValueError:
        return False
    return True


#: The handler of each Viva ``EventTypeId`` an order receives, per Viva's
#: documentation (Transaction Payment Created, Reversal Created, Failed and
#: Price Calculated, the commission), with what it reads from Viva before
#: the order row is locked.
_EVENT_HANDLERS: dict[
    int, tuple[viva_settlement.EventHandler, viva_settlement.EventRead]
] = {
    viva_settlement.VIVA_TRANSACTION_PAYMENT_CREATED: (
        viva_settlement.handle_payment_created,
        viva_settlement.read_payment_created,
    ),
    1797: (_handle_reversal_created, _read_reversal),
    1798: (_handle_payment_failed, _read_event_transaction),
    VIVA_TRANSACTION_PRICE_CALCULATED: (
        _handle_transaction_price_calculated,
        _read_event_transaction,
    ),
}
