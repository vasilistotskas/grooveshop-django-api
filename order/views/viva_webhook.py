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

logger = logging.getLogger(__name__)


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
    verification_key = getattr(
        settings, "VIVA_WALLET_WEBHOOK_VERIFICATION_KEY", ""
    )

    if not verification_key:
        verification_key = _fetch_verification_key()

    if not verification_key:
        logger.error("Viva Wallet webhook verification key unavailable")
        return JsonResponse({"error": "Not configured"}, status=500)

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


def _verify_transaction(transaction_id):
    from order.payment import VivaWalletPaymentProvider

    try:
        provider = VivaWalletPaymentProvider()
        status, data = provider.get_payment_status(transaction_id)
        return status, data
    except Exception:
        logger.exception(
            "Failed to verify Viva Wallet transaction: %s",
            transaction_id,
        )
        return None, {}


def _handle_webhook_event(request):
    from django.db import transaction

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        logger.error("Invalid JSON in Viva Wallet webhook")
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    event_data = body.get("EventData", {})
    event_type_id = body.get("EventTypeId")

    transaction_id = event_data.get("TransactionId", "")
    order_code = event_data.get("OrderCode")
    status_id = event_data.get("StatusId", "")

    logger.info(
        "Viva Wallet webhook received",
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
            "Order not found for Viva Wallet order code: %s",
            order_code,
        )
        return JsonResponse({"status": "ok"})

    event_key = f"viva_webhook_{transaction_id}_{event_type_id}"
    if order.metadata and order.metadata.get(event_key):
        logger.info(
            "Viva Wallet webhook already processed: %s",
            event_key,
        )
        return JsonResponse({"status": "ok"})

    with transaction.atomic():
        # Re-fetch with row lock to prevent race conditions
        order = Order.objects.select_for_update().get(pk=order.pk)

        # Double-check idempotency after acquiring lock
        if order.metadata and order.metadata.get(event_key):
            return JsonResponse({"status": "ok"})

        if not order.metadata:
            order.metadata = {}
        order.metadata[event_key] = True

        # Event type IDs per Viva documentation:
        # 1796 = Transaction Payment Created
        # 1797 = Transaction Reversal Created
        # 1798 = Transaction Failed
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

    return JsonResponse({"status": "ok"})


def _handle_payment_created(order, event_data, transaction_id):
    from django.utils import timezone

    logger.info(
        "Viva Wallet payment created for order %s",
        order.id,
    )

    if transaction_id:
        verified_status, verified_data = _verify_transaction(transaction_id)
        if verified_status and verified_status != PaymentStatus.COMPLETED:
            logger.warning(
                "Viva transaction %s status mismatch: %s",
                transaction_id,
                verified_status,
            )

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
    ]

    if order.status == OrderStatus.PENDING:
        order.status = OrderStatus.PROCESSING
        order.status_updated_at = timezone.now()
        update_fields += ["status", "status_updated_at"]

    order.save(update_fields=update_fields)

    OrderHistory.log_payment_update(
        order=order,
        previous_value={"payment_status": "pending"},
        new_value={
            "payment_status": "completed",
            "payment_id": transaction_id,
            "provider": "viva_wallet",
        },
    )


def _handle_payment_failed(order, event_data, transaction_id):
    logger.info(
        "Viva Wallet payment failed for order %s",
        order.id,
    )

    order.payment_status = PaymentStatus.FAILED
    order.save(update_fields=["payment_status", "metadata"])

    OrderHistory.log_payment_update(
        order=order,
        previous_value={"payment_status": "pending"},
        new_value={
            "payment_status": "failed",
            "payment_id": transaction_id,
            "provider": "viva_wallet",
        },
    )


def _handle_reversal_created(order, event_data, transaction_id):
    logger.info(
        "Viva Wallet reversal created for order %s",
        order.id,
    )

    previous_payment_status = order.payment_status

    order.payment_status = PaymentStatus.REFUNDED
    order.status = OrderStatus.REFUNDED
    order.save(update_fields=["payment_status", "status", "metadata"])

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
