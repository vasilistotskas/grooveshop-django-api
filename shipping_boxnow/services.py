from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from shipping.services import DELIVERY_NOTES_MAX_LEN
from shipping_boxnow.client import BoxNowClient
from shipping_boxnow.enum import BoxNowParcelState, BoxNowPaymentMode
from shipping_boxnow.exceptions import BoxNowAPIError
from shipping_boxnow.models import (
    BoxNowLocker,
    BoxNowParcelEvent,
    BoxNowShipment,
)

if TYPE_CHECKING:
    from order.models.order import Order

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State-transition tables
# ---------------------------------------------------------------------------
# Using string literals to avoid importing order.enum.status at module level
# (would risk a circular import since order.services lazy-imports shipping_boxnow).

# BoxNow webhook events that should advance the Order to SHIPPED.
# Only applied when the order has not yet reached SHIPPED / a later state.
_SHIPPED_EVENTS: frozenset[str] = frozenset(
    {
        BoxNowParcelState.FINAL_DESTINATION,
        BoxNowParcelState.ACCEPTED_TO_LOCKER,
    }
)

# Order statuses that are "earlier" than SHIPPED — the only ones from which
# we are allowed to advance forward to SHIPPED.
_PRE_SHIPPED_STATUSES: frozenset[str] = frozenset({"PENDING", "PROCESSING"})

# Terminal Order statuses — never move backwards from these.
_TERMINAL_ORDER_STATUSES: frozenset[str] = frozenset(
    {"DELIVERED", "COMPLETED", "CANCELED", "RETURNED", "REFUNDED"}
)

# Label cache TTL in seconds (1 hour).
_LABEL_CACHE_TTL = 3600

# Subset of the deliveryRequest payload we mirror onto ``shipment.metadata
# ['create_request']`` so a future "did X actually leave us?" question
# (description/notes, COD amount, locker id, weight, …) can be answered
# from the DB without re-running the payload builder. Strictly
# operational — destination/origin contact details are intentionally
# excluded because shipment.metadata is not cleared by GDPR
# anonymisation (see user/services/gdpr.py).
_AUDIT_REQUEST_FIELDS: tuple[str, ...] = (
    "orderNumber",
    "description",
    "invoiceValue",
    "paymentMode",
    "amountToBeCollected",
)


def _audit_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a PII-scrubbed copy of the BoxNow deliveryRequest payload.

    Includes the operational subset plus ``destination.locationId``
    (locker id — not PII) and the ``items`` array (weights /
    compartment size / value — not PII). Keys mirror the BoxNow
    payload shape (camelCase) so a reader who knows the API can
    grep this envelope by the same field names.
    """
    envelope: dict[str, Any] = {
        k: payload[k] for k in _AUDIT_REQUEST_FIELDS if k in payload
    }
    dest = payload.get("destination") or {}
    if "locationId" in dest:
        envelope["destination"] = {"locationId": dest["locationId"]}
    if payload.get("items"):
        envelope["items"] = payload["items"]
    return envelope


# BoxNow's voucher template prints ``items[].weight`` directly with a
# ``kg`` label and 2-decimal formatting — empirically verified by
# inspecting a stage voucher: sending ``189`` printed ``189.00 kg`` (NOT
# 0.189 kg). So the field is **kilograms**, not grams. The P421 cap
# (0..10^6) therefore caps kilograms — generous, but BoxNow's max
# physical compartment is ~30 kg so any sane parcel passes through
# the bound easily.
_BOXNOW_MAX_WEIGHT_KG = 1_000_000.0


def _format_parcel_weight_kg(weight_grams: int | None) -> float:
    """Convert internal grams → BoxNow's kilogram-decimal payload value.

    Internally we store weight as integer grams (``weight_grams``) for
    precision. BoxNow's delivery-request API expects ``items[].weight``
    in **kilograms** (their voucher template stamps the value verbatim
    as ``N.NN kg``), so we divide by 1000 and round to 3 decimals
    (gram-level precision while staying inside BoxNow's expected
    decimal scale).

    A None / zero / negative input becomes ``0.0`` per BoxNow PDF
    §3.4 ("if parcel weight unknown pass 0"). Values above the P421
    cap are clamped + logged so a single corrupt row can't dead-letter
    the whole BoxNow Celery task fan-out.
    """
    if weight_grams is None or weight_grams <= 0:
        return 0.0
    weight_kg = round(weight_grams / 1000.0, 3)
    if weight_kg > _BOXNOW_MAX_WEIGHT_KG:
        logger.warning(
            "Clamping BoxNow parcel weight %s kg to BoxNow's max %s kg "
            "(P421 prevention). Likely a unit mix-up upstream — check "
            "the product's MeasurementField unit.",
            weight_kg,
            _BOXNOW_MAX_WEIGHT_KG,
        )
        return _BOXNOW_MAX_WEIGHT_KG
    return weight_kg


class BoxNowService:
    """
    Application-level orchestration for BoxNow parcel deliveries.

    All public methods are classmethods so callers never need to
    instantiate the service; the service itself instantiates
    ``BoxNowClient`` on demand.

    Concurrency model
    -----------------
    Any method that modifies shared rows (``BoxNowShipment``,
    ``BoxNowParcelEvent``, ``Order``) uses ``select_for_update()``
    before entering the state-change block.  This prevents races
    between simultaneous Celery task runs and incoming webhook
    deliveries.
    """

    # ------------------------------------------------------------------
    # create_shipment_for_order
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 3-phase mint design constants — mirrors AcsService
    # ------------------------------------------------------------------

    # How long another Celery worker waits before re-attempting a mint
    # that another instance has already started. Larger than the BoxNow
    # HTTP timeout + retry budget so a healthy mint always wins; shorter
    # than the Celery soft-timeout so a crashed worker never permanently
    # strands a shipment. Mirrors AcsService — see the rationale there
    # for the 300s sizing (stage order 682, 2026-04-29).
    _MINT_CLAIM_TTL_SECONDS = 300

    @classmethod
    def create_shipment_for_order(cls, order: Order) -> BoxNowShipment:
        """
        Call BoxNow's delivery-request API and persist the IDs on the
        shipment row.

        Three-phase design — mirrors :meth:`AcsService.create_voucher_for_order`
        — that survives a connection-level failure between the API
        success and the local save:

        1. **Claim** — short atomic block: lock the shipment row,
           short-circuit on existing ``delivery_request_id``, and stamp
           ``metadata['mint_started_at']``. The claim prevents two
           concurrent Celery workers from both POSTing to BoxNow and
           minting duplicate parcels.
        2. **API call** — no DB lock, no open transaction. Network
           latency or ``idle_in_transaction_session_timeout`` can no
           longer roll back the local save after BoxNow already minted
           the parcel — the orphan-parcel risk goes away.
        3. **Persist** — fresh atomic + ``select_for_update`` race
           check. If another worker has already saved a delivery_request_id
           (the claim TTL expired during a slow API), our request becomes
           the duplicate and is recorded for manual reconciliation
           (BoxNow has no programmatic "delete delivery request" before
           it's finalised; ``cancel_shipment`` is the cleanup path).

        Idempotency on ``delivery_request_id``: if the shipment already
        has one, return it unchanged. BoxNow's P410 (duplicate order
        number) would otherwise surface as a non-retryable error.
        """
        # ---- Phase 1: claim ----------------------------------------------
        with transaction.atomic():
            try:
                shipment: BoxNowShipment = (
                    BoxNowShipment.objects.select_for_update()
                    .select_related("order")
                    .get(order=order)
                )
            except BoxNowShipment.DoesNotExist as exc:
                raise ValueError(
                    f"Order {order.id} has no BoxNow shipment row. "
                    "Create a BoxNowShipment at order-creation time."
                ) from exc

            if shipment.delivery_request_id:
                logger.info(
                    "create_shipment_for_order: order=%s already has "
                    "delivery_request_id=%s — returning existing shipment",
                    order.id,
                    shipment.delivery_request_id,
                )
                return shipment

            metadata = shipment.metadata or {}
            started_raw = metadata.get("mint_started_at")
            if started_raw:
                started = parse_datetime(str(started_raw))
                if started is not None:
                    if started.tzinfo is None:
                        started = timezone.make_aware(started)
                    age = (timezone.now() - started).total_seconds()
                    if age < cls._MINT_CLAIM_TTL_SECONDS:
                        # Another worker is mid-flight. Surface as
                        # retryable so Celery backs off and re-checks.
                        # ``status_code=409`` matches the semantic
                        # ("conflict — work already in progress");
                        # nothing inspects it for routing.
                        from shipping_boxnow.exceptions import (
                            BoxNowRetryableError,
                        )

                        raise BoxNowRetryableError(
                            409,
                            message=(
                                f"BoxNow mint already in flight for order "
                                f"{order.id} (started {age:.1f}s ago) — "
                                "deferring to that worker."
                            ),
                        )
                    # TTL expired but no delivery_request_id was saved.
                    # A prior worker likely crashed AFTER BoxNow created
                    # the parcel but BEFORE Phase 3 saved. The next
                    # mint may produce an orphan parcel on BoxNow's
                    # side. Log loudly so ops can reconcile against
                    # BoxNow's dashboard by orderNumber=order.id.
                    logger.warning(
                        "create_shipment_for_order: stale mint_started_at "
                        "for order=%s (age=%.1fs > TTL=%ds) without a "
                        "delivery_request_id — re-minting. Check BoxNow "
                        "dashboard for a prior orphan parcel under "
                        "orderNumber=%s.",
                        order.id,
                        age,
                        cls._MINT_CLAIM_TTL_SECONDS,
                        order.id,
                        extra={
                            "order_id": order.id,
                            "shipment_pk": shipment.pk,
                            "carrier": "boxnow",
                            "phase": "phase1_stale_claim",
                            "claim_age_seconds": age,
                        },
                    )

            shipment.metadata = {
                **metadata,
                "mint_started_at": timezone.now().isoformat(),
            }
            update_fields = ["metadata", "updated_at"]

            # COD amount is filled in here (not at row creation) because
            # ``order.total_price`` requires order items to be persisted
            # — which happens after the create_shipment_row hook fires.
            # Idempotent: re-runs see the existing non-zero amount and
            # skip the recompute.
            if (
                shipment.payment_mode == BoxNowPaymentMode.COD
                and shipment.amount_to_be_collected.amount == 0
            ):
                shipment.amount_to_be_collected = order.total_price
                update_fields.extend(
                    [
                        "amount_to_be_collected",
                        "amount_to_be_collected_currency",
                    ]
                )

            shipment.save(update_fields=update_fields)

        # ---- Phase 2: API call (no transaction, no lock) -----------------
        notify_phone = getattr(settings, "BOXNOW_NOTIFY_PHONE", "+302100000000")
        site_name = getattr(settings, "SITE_NAME", "GrooveShop")
        warehouse_id = str(getattr(settings, "BOXNOW_WAREHOUSE_ID", "2"))
        paid_amount = getattr(order, "paid_amount", None)
        invoice_value = str(paid_amount.amount) if paid_amount else "0.00"
        # ``notifyOnAccepted`` is the PARTNER (operations) email per
        # BoxNow API §3.4 — BoxNow sends us the success-notification +
        # PDF label here. The customer gets BoxNow's own customer-facing
        # notifications via ``destination.contactEmail`` / ``contactNumber``.
        partner_notify_email = getattr(settings, "INFO_EMAIL", None) or getattr(
            settings, "DEFAULT_FROM_EMAIL", ""
        )

        # BoxNow P408 rejects ``amountToBeCollected`` outside (0, 5000).
        # For PREPAID parcels the courier shouldn't collect anything;
        # force ``"0.00"`` so a prepaid cart over €5000 still mints.
        # COD parcels carry the real amount and are pre-validated at
        # row creation time (carrier.create_shipment_row).
        if shipment.payment_mode == BoxNowPaymentMode.PREPAID:
            amount_to_collect_str = "0.00"
        else:
            amount_to_collect_str = str(shipment.amount_to_be_collected.amount)

        # Anchor log for the COD-vs-PREPAID decision so a future
        # "why did the locker collect €0 / the wrong amount?" question
        # can be answered from logs alone. ``payment_mode`` is the
        # canonical discriminator; ``amount_to_be_collected`` is what
        # the courier will actually charge at the locker.
        logger.info(
            "BoxNow deliveryRequest amount: order=%s payment_mode=%s "
            "amount_to_collect=%s",
            order.id,
            shipment.payment_mode,
            amount_to_collect_str,
            extra={
                "order_id": order.id,
                "carrier": "boxnow",
                "payment_mode": shipment.payment_mode,
                "amount_to_collect": amount_to_collect_str,
                "pay_way_code": getattr(
                    getattr(order, "pay_way", None),
                    "provider_code",
                    None,
                ),
            },
        )

        # BoxNow's ``description`` field carries free-text per-delivery
        # context (per API Manual §6.3.1 example). For us that's the
        # customer's checkout note ("ring twice", "leave with porter")
        # — captured on ``Order.customer_notes`` and previously dropped
        # on the floor between Django and the carrier (reported by the
        # site owner 2026-05-16). Shared helper trims + caps to keep
        # the partner-portal box readable.
        from shipping.services import sanitize_delivery_notes

        payload: dict[str, Any] = {
            "orderNumber": str(order.id),
            "description": sanitize_delivery_notes(order.customer_notes),
            "invoiceValue": invoice_value,
            "paymentMode": shipment.payment_mode,
            "amountToBeCollected": amount_to_collect_str,
            "notifyOnAccepted": partner_notify_email,
            "origin": {
                "contactNumber": notify_phone,
                "contactEmail": partner_notify_email,
                "contactName": site_name,
                "locationId": warehouse_id,
            },
            "destination": {
                "contactNumber": str(order.phone) if order.phone else "",
                "contactEmail": order.email or "",
                "contactName": (
                    f"{order.first_name} {order.last_name}".strip()
                ),
                "locationId": shipment.locker_external_id,
            },
            "items": [
                {
                    "id": str(order.id),
                    "name": "voucher",
                    "value": invoice_value,
                    "compartmentSize": shipment.compartment_size,
                    # BoxNow API §3.4 expects parcel weight in
                    # **kilograms** (decimal). Empirically: their voucher
                    # template prints the raw value with a ``kg`` label
                    # and 2-decimal formatting, so 189 g → ``189.00 kg``
                    # (NOT 0.19 kg). We store grams internally for
                    # precision and convert to kilograms here.
                    "weight": _format_parcel_weight_kg(shipment.weight_grams),
                }
            ],
        }

        logger.info(
            "create_shipment_for_order: creating delivery request "
            "for order=%s locker=%s",
            order.id,
            shipment.locker_external_id,
        )
        try:
            response = BoxNowClient().create_delivery_request(payload)
        except BoxNowAPIError as exc:
            logger.error(
                "BoxNow API error creating delivery request for order %s: %s",
                order.id,
                exc,
                extra={
                    "order_id": order.id,
                    "boxnow_code": exc.code,
                    "boxnow_message": exc.message,
                },
            )
            cls._release_mint_claim(
                shipment.pk,
                last_error={
                    "type": "BoxNowAPIError",
                    "code": exc.code,
                    "message": exc.message,
                    "status_code": exc.status_code,
                    "response_text": exc.response_text[:500],
                },
            )
            raise
        except Exception:
            # Connection error / retryable — drop the claim so the
            # next Celery retry can re-acquire after the TTL expires.
            cls._release_mint_claim(shipment.pk)
            raise

        delivery_request_id: str = response["id"]
        parcel_id: str = response["parcels"][0]["id"]

        # ---- Phase 3: persist atomically ---------------------------------
        with transaction.atomic():
            shipment = (
                BoxNowShipment.objects.select_for_update()
                .select_related("order")
                .get(pk=shipment.pk)
            )

            if (
                shipment.delivery_request_id
                and shipment.delivery_request_id != delivery_request_id
            ):
                # Another worker raced past the TTL and persisted a
                # different delivery request. Stash ours in metadata
                # for manual reconciliation; return the saved row.
                logger.error(
                    "create_shipment_for_order: race detected for order=%s "
                    "— another worker persisted delivery_request_id=%s "
                    "while we minted %s. Recording orphan for ops review.",
                    order.id,
                    shipment.delivery_request_id,
                    delivery_request_id,
                )
                orphans = shipment.metadata.get("orphan_delivery_requests", [])
                orphans.append(
                    {
                        "delivery_request_id": delivery_request_id,
                        "parcel_id": parcel_id,
                        "minted_at": timezone.now().isoformat(),
                    }
                )
                shipment.metadata = {
                    **shipment.metadata,
                    "orphan_delivery_requests": orphans,
                    "mint_started_at": None,
                }
                shipment.save(update_fields=["metadata", "updated_at"])
                return shipment

            shipment.delivery_request_id = delivery_request_id
            shipment.parcel_id = parcel_id
            shipment.parcel_state = BoxNowParcelState.NEW
            shipment.last_event_at = timezone.now()
            shipment.metadata = {
                **shipment.metadata,
                "create_response": response,
                "create_request": _audit_envelope(payload),
                "last_error": None,
                "mint_started_at": None,
            }
            shipment.save(
                update_fields=[
                    "delivery_request_id",
                    "parcel_id",
                    "parcel_state",
                    "last_event_at",
                    "metadata",
                    "updated_at",
                ]
            )

            # ``add_tracking_info`` issues its own save inside this txn.
            order.add_tracking_info(
                tracking_number=parcel_id,
                shipping_carrier="boxnow",
            )
            cls._advance_pending_order_to_processing(order)

        sent_notes = payload.get("description", "") or ""
        raw_notes_len = len(order.customer_notes or "")
        # ``notes_truncated`` derives from the post-sanitize length —
        # mirrors AcsService for the same reason.
        logger.info(
            "create_shipment_for_order: parcel minted for order=%s "
            "delivery_request_id=%s parcel_id=%s",
            order.id,
            delivery_request_id,
            parcel_id,
            extra={
                "order_id": order.id,
                "shipment_pk": shipment.pk,
                "carrier": "boxnow",
                "phase": "phase3_minted",
                "delivery_request_id": delivery_request_id,
                "parcel_id": parcel_id,
                "notes_chars_raw": raw_notes_len,
                "notes_chars_sent": len(sent_notes),
                "notes_truncated": len(sent_notes) >= DELIVERY_NOTES_MAX_LEN,
            },
        )
        return shipment

    @classmethod
    def _release_mint_claim(
        cls, shipment_pk: int, *, last_error: dict | None = None
    ) -> None:
        """Drop the mint claim after a Phase-2 failure.

        Best-effort — uses an inner ``select_for_update`` so a healthy
        worker that grabbed the lock between Phase 1 and Phase 3 isn't
        clobbered. ``last_error`` is stashed for ops diagnostics when
        the failure is a non-retryable BoxNow API error.
        """
        with transaction.atomic():
            shipment = (
                BoxNowShipment.objects.select_for_update()
                .filter(pk=shipment_pk)
                .first()
            )
            if shipment is None:
                return
            update: dict[str, Any] = {"mint_started_at": None}
            if last_error is not None:
                update["last_error"] = last_error
            shipment.metadata = {**shipment.metadata, **update}
            shipment.save(update_fields=["metadata", "updated_at"])

    # ------------------------------------------------------------------
    # cancel_shipment
    # ------------------------------------------------------------------

    @classmethod
    def cancel_shipment(
        cls, shipment: BoxNowShipment, *, reason: str = ""
    ) -> None:
        """
        Cancel a BoxNow parcel delivery.

        BoxNow only permits cancellation while the parcel is in the
        ``NEW`` state (error P420 otherwise). We mirror that guard
        locally so we never make an API call that BoxNow would reject,
        and so the caller gets a clear error message.

        Three-phase design — same shape as ``create_shipment_for_order``
        — so the BoxNow API call doesn't happen with a row lock held.
        Without it, a slow BoxNow response under
        ``idle_in_transaction_session_timeout=10000ms`` would abort
        the txn AFTER BoxNow had already cancelled the parcel,
        leaving the DB showing parcel_state=NEW while BoxNow had
        marked it CANCELED — subsequent retries would hit P420
        ("not in cancellable state") and surface that as a real error.

        Phase 1 — short atomic: lock + state guard + parcel_id read.
        Phase 2 — API call without lock or transaction.
        Phase 3 — short atomic: lock again, persist cancellation.

        Args:
            shipment: The ``BoxNowShipment`` to cancel.
            reason:   Human-readable reason for the cancellation
                      (stored in ``shipment.metadata["cancellations"]``).

        Raises:
            BoxNowAPIError: Shipment has a ``parcel_id`` but is not in
                the ``NEW`` state (P420).
            BoxNowAPIError: BoxNow API rejected the request.
        """
        # ---- Phase 1: state guard + parcel_id snapshot ------------------
        with transaction.atomic():
            locked: BoxNowShipment = (
                BoxNowShipment.objects.select_for_update().get(pk=shipment.pk)
            )
            parcel_id = locked.parcel_id

            # No parcel_id ⇒ nothing at BoxNow to cancel. This covers
            # the canonical ``pending_creation`` case (online order
            # never paid, admin cancels — BoxNow create-shipment task
            # never ran) AND the rarer ``NEW`` + crashed-mint case
            # (Phase 3 persist crashed before saving the IDs).
            # Either way: mark CANCELED locally, no API call.
            #
            # The state guard further down only fires when ``parcel_id``
            # is set — i.e. there is a real BoxNow-side parcel that has
            # progressed past NEW and can't safely be cancelled remotely.
            # Verified against prod order 76 (2026-05-21): the admin
            # form-save cancel on an unpaid BoxNow order in
            # ``pending_creation`` previously stored a confusing
            # ``BoxNow API 409 [P420]`` error in
            # ``metadata.cancellation.shipment_cancel.error`` even
            # though the order cancel itself succeeded.
            if not parcel_id:
                logger.info(
                    "cancel_shipment: shipment=%s has no parcel_id "
                    "(pending_creation or crashed mint) — marking "
                    "CANCELED locally without an API call.",
                    shipment.pk,
                    extra={
                        "shipment_pk": shipment.pk,
                        "order_id": locked.order_id,
                        "carrier": "boxnow",
                    },
                )
                locked.cancel_requested_at = timezone.now()
                locked.parcel_state = BoxNowParcelState.CANCELED
                cancellations: list[dict] = locked.metadata.get(
                    "cancellations", []
                )
                cancellations.append(
                    {
                        "reason": reason,
                        "cancelled_at": locked.cancel_requested_at.isoformat(),
                        "note": "no parcel_id — local-only cancel",
                    }
                )
                locked.metadata = {
                    **locked.metadata,
                    "cancellations": cancellations,
                }
                locked.save(
                    update_fields=[
                        "cancel_requested_at",
                        "parcel_state",
                        "metadata",
                        "updated_at",
                    ]
                )
                return

            # State guard: parcel_id is set, so a real BoxNow-side
            # parcel exists. BoxNow only allows cancellation from
            # the NEW state — refuse remote cancel for anything else.
            if locked.parcel_state != BoxNowParcelState.NEW:
                raise BoxNowAPIError(
                    409,
                    code="P420",
                    message=(
                        "Parcel not cancellable in current state "
                        f"({locked.parcel_state!r}). "
                        "BoxNow only permits cancellation from the NEW state."
                    ),
                )

        # ---- Phase 2: API call (no transaction, no lock) ----------------
        logger.info(
            "cancel_shipment: cancelling parcel_id=%s reason=%r",
            parcel_id,
            reason,
        )
        BoxNowClient().cancel_parcel(parcel_id)

        # ---- Phase 3: persist cancellation atomically -------------------
        with transaction.atomic():
            locked = BoxNowShipment.objects.select_for_update().get(
                pk=shipment.pk
            )

            # Idempotency: if a webhook beat us to it (BoxNow's own
            # cancellation event), the row is already CANCELED — fine.
            if locked.parcel_state == BoxNowParcelState.CANCELED:
                logger.info(
                    "cancel_shipment: parcel_id=%s was already marked "
                    "CANCELED locally — skipping persist phase.",
                    parcel_id,
                )
                return

            locked.cancel_requested_at = timezone.now()
            locked.parcel_state = BoxNowParcelState.CANCELED

            cancellations: list[dict] = locked.metadata.get("cancellations", [])
            cancellations.append(
                {
                    "reason": reason,
                    "cancelled_at": locked.cancel_requested_at.isoformat(),
                }
            )
            locked.metadata = {
                **locked.metadata,
                "cancellations": cancellations,
            }

            locked.save(
                update_fields=[
                    "cancel_requested_at",
                    "parcel_state",
                    "metadata",
                    "updated_at",
                ]
            )

        logger.info(
            "cancel_shipment: parcel_id=%s cancelled successfully",
            parcel_id,
        )

    # ------------------------------------------------------------------
    # apply_webhook_event
    # ------------------------------------------------------------------

    @classmethod
    def apply_webhook_event(cls, envelope: dict) -> BoxNowParcelEvent | None:
        """
        Process a verified BoxNow CloudEvents webhook envelope.

        Why this method is not itself decorated with @transaction.atomic
        ----------------------------------------------------------------
        The outer idempotency check (``BoxNowParcelEvent`` already exists)
        must be visible to all DB connections before we enter the inner
        atomic block.  Wrapping everything in a single outer transaction
        would cause the idempotency SELECT to read uncommitted state from
        a concurrent call in the same outer transaction.  Instead the
        inner ``transaction.atomic()`` context manager handles
        consistency for the state-changing work only.

        Idempotency
        -----------
        ``webhook_message_id`` maps to the CloudEvents ``id`` field and
        is a unique DB column.  If a record for that ID already exists
        we return ``None`` immediately — BoxNow will receive a 200 OK so
        it stops retrying.

        Out-of-order delivery
        ---------------------
        BoxNow's retry mechanism can deliver events out of chronological
        order.  We only update ``shipment.parcel_state`` when the event's
        ``event_time`` is newer than ``shipment.last_event_at``, so a
        late-arriving ``in-depot`` event never overwrites an already
        applied ``delivered`` state.

        Args:
            envelope: The full parsed webhook JSON body.

        Returns:
            The created ``BoxNowParcelEvent``, or ``None`` if the event
            was already processed (idempotent no-op).
        """
        data: dict = envelope["data"]
        message_id: str = envelope["id"]

        # --- Idempotency check (outside transaction) ----------------------
        if BoxNowParcelEvent.objects.filter(
            webhook_message_id=message_id
        ).exists():
            logger.info(
                "apply_webhook_event: message_id=%s already processed — "
                "skipping",
                message_id,
            )
            return None

        # --- Pre-check shipment exists (no lock — autocommit) ----------
        parcel_id: str = data["parcelId"]
        if not BoxNowShipment.objects.filter(parcel_id=parcel_id).exists():
            # BoxNow may fire hooks before we have finished persisting
            # the shipment row (race between create_shipment_for_order
            # task and the first incoming hook).  Log and return — the
            # webhook view will still return 200 OK.
            logger.warning(
                "apply_webhook_event: no BoxNowShipment found for "
                "parcel_id=%s (message_id=%s) — ignoring",
                parcel_id,
                message_id,
            )
            return None

        # --- Map webhook event to our enum -------------------------------
        # `event_type` is a CharField so it accepts either an enum
        # member's `.value` (the canonical case) or the raw webhook
        # string when BoxNow ships a state we haven't mapped yet —
        # narrow to `str` either way and store that.
        raw_event: str = data["event"]
        mapped_state: str
        try:
            mapped_state = BoxNowParcelState.from_webhook_event(raw_event)
        except ValueError:
            logger.warning(
                "apply_webhook_event: unknown BoxNow event %r for "
                "parcel_id=%s — storing as-is and continuing",
                raw_event,
                parcel_id,
            )
            mapped_state = raw_event

        # --- Parse event timestamp ---------------------------------------
        event_time = parse_datetime(data["time"])

        # --- Atomic block: lock shipment + create event + update order ---
        # ``select_for_update`` MUST live inside ``transaction.atomic``
        # — outside, under autocommit, the lock is released the instant
        # the SELECT statement completes (silent no-op). The previous
        # code took the lock outside the block and then read the stale
        # ``shipment`` reference inside, leaving the state-update path
        # racy under two concurrent webhooks for the same parcel.
        with transaction.atomic():
            try:
                shipment: BoxNowShipment = (
                    BoxNowShipment.objects.select_for_update()
                    .select_related("order")
                    .get(parcel_id=parcel_id)
                )
            except BoxNowShipment.DoesNotExist:
                # Disappeared between the pre-check and the lock — same
                # outcome: skip and let BoxNow retry the webhook.
                logger.warning(
                    "apply_webhook_event: BoxNowShipment for parcel_id=%s "
                    "vanished between pre-check and lock (message_id=%s)",
                    parcel_id,
                    message_id,
                )
                return None

            # Re-check idempotency inside the transaction in case a
            # concurrent request inserted the row after our outer check.
            event, created = BoxNowParcelEvent.objects.get_or_create(
                webhook_message_id=message_id,
                defaults={
                    "shipment": shipment,
                    "event_type": mapped_state,
                    "parcel_state": data.get("parcelState", ""),
                    "event_time": event_time,
                    "display_name": (
                        data.get("eventLocation", {}).get("displayName", "")
                    ),
                    "postal_code": (
                        data.get("eventLocation", {}).get("postalCode", "")
                    ),
                    "additional_information": data.get(
                        "additionalInformation", ""
                    ),
                    "raw_payload": envelope,
                },
            )

            if not created:
                # Race: another worker beat us here between the outer
                # check and the get_or_create call.
                logger.info(
                    "apply_webhook_event: message_id=%s inserted by "
                    "concurrent worker — no-op",
                    message_id,
                )
                return None

            # --- Update shipment state (out-of-order protection) ---------
            if shipment.last_event_at is None or (
                event_time is not None and event_time > shipment.last_event_at
            ):
                shipment.parcel_state = mapped_state
                shipment.last_event_at = event_time
                shipment.save(
                    update_fields=[
                        "parcel_state",
                        "last_event_at",
                        "updated_at",
                    ]
                )
                logger.info(
                    "apply_webhook_event: parcel_id=%s state → %s "
                    "(event_time=%s)",
                    parcel_id,
                    mapped_state,
                    event_time,
                )
            else:
                logger.info(
                    "apply_webhook_event: parcel_id=%s out-of-order "
                    "event %r (event_time=%s ≤ last_event_at=%s) — "
                    "shipment state NOT updated",
                    parcel_id,
                    raw_event,
                    event_time,
                    shipment.last_event_at,
                )

            # --- Map BoxNow event to Order status transition --------------
            cls._apply_order_status_transition(shipment, mapped_state)

            # --- Enqueue arrival notification for final-destination -------
            # Wrap ``.delay`` in ``transaction.on_commit`` so the Celery
            # worker only sees the BoxNowParcelEvent row AFTER this
            # atomic block commits. Without the wrapper the worker can
            # dequeue before commit and crash on ``DoesNotExist`` (same
            # class of bug as ACS order 47 — see project memory
            # ``project_shipping_dispatch_on_commit``).
            if mapped_state == BoxNowParcelState.FINAL_DESTINATION:
                try:
                    from shipping_boxnow.tasks import (  # noqa: PLC0415
                        boxnow_send_arrival_notification,
                    )

                    transaction.on_commit(
                        lambda eid=event.id: (
                            boxnow_send_arrival_notification.delay(eid)
                        )
                    )
                except ImportError:
                    logger.warning(
                        "apply_webhook_event: "
                        "boxnow_send_arrival_notification task not yet "
                        "available (Wave 3 task); skipping notification "
                        "for event.id=%s",
                        event.id,
                    )

            # --- Inline WebSocket notification on parcel delivery ---------
            # No email here — the customer already gets order-status emails
            # from the order app. This single Notification row + its post-save
            # signal fan-out is enough for the BOXNOW_PARCEL_DELIVERED toast
            # the Nuxt websocket plugin wires up.
            elif mapped_state == BoxNowParcelState.DELIVERED:
                cls._dispatch_delivered_notification(shipment)

        return event

    # ------------------------------------------------------------------
    # _dispatch_delivered_notification (private helper)
    # ------------------------------------------------------------------

    @staticmethod
    def _dispatch_delivered_notification(shipment: BoxNowShipment) -> None:
        """Create a BOXNOW_PARCEL_DELIVERED in-app notification.

        Fast-path inline dispatch (no Celery task) since this only writes
        a single row and the post-save signal handles the WebSocket fan-out.
        Failures are logged but never propagate — the webhook signature was
        already verified, so we always want to return 200 to BoxNow.
        """
        order = shipment.order
        if not order.user_id:
            # Guest order — no in-app notification target.
            return

        try:
            from notification.enum import (  # noqa: PLC0415
                NotificationCategoryEnum,
                NotificationKindEnum,
                NotificationPriorityEnum,
                NotificationTypeEnum,
            )
            from notification.services import (  # noqa: PLC0415
                create_user_notification,
            )

            translations = {
                "el": {
                    "title": "Το πακέτο παραλήφθηκε",
                    "message": (
                        "Παραλάβατε το πακέτο σας από το BOX NOW locker"
                    ),
                },
            }
            create_user_notification(
                user=order.user,
                translations=translations,
                kind=NotificationKindEnum.SUCCESS,
                category=NotificationCategoryEnum.SHIPPING,
                priority=NotificationPriorityEnum.NORMAL,
                notification_type=NotificationTypeEnum.BOXNOW_PARCEL_DELIVERED,
                link=f"/account/orders/{order.id}",
            )
        except Exception:
            logger.exception(
                "BoxNow delivered notification failed for order %s "
                "(parcel=%s) — webhook still returns 200",
                order.id,
                shipment.parcel_id,
            )

    # ------------------------------------------------------------------
    # sync_shipment_state — REST poll fallback (defence-in-depth)
    # ------------------------------------------------------------------

    # Synthetic message-id prefix for events recorded via the REST poll
    # path instead of a CloudEvents webhook delivery. Stays compatible
    # with the unique-key idempotency contract on
    # ``BoxNowParcelEvent.webhook_message_id`` while making the source
    # of each row traceable in audits.
    _POLL_MESSAGE_ID_PREFIX = "poll"

    @classmethod
    def sync_shipment_state(cls, shipment: BoxNowShipment) -> dict[str, Any]:
        """Reconcile one shipment with the BoxNow REST API.

        Why this exists
        ---------------
        BoxNow's webhook URL is **partner-managed on BoxNow's side**
        (per Webhook-Based Parcel Tracking Guide v1.4 §"Hook
        Management": _"BOX NOW will assist partners in configuring
        their webhook to the appropriate URL"_). There is no
        self-serve registration. If their support hasn't wired our
        URL yet — or a single delivery is silently dropped — the
        webhook path goes dark and parcel state freezes at whatever
        we last observed (e.g. ``new`` after creation). A 15-minute
        poll converges state independent of webhook health.

        How it stays consistent with the webhook path
        ---------------------------------------------
        * Each event row inserted carries a deterministic synthetic
          ``webhook_message_id`` of the form
          ``poll:{parcel_id}:{event_type}:{event_time_iso}``. The
          unique constraint on the column means a re-poll of the
          same parcel is idempotent — duplicates collapse silently.
        * Before inserting we also check for an event matching
          ``(shipment, event_type, event_time)`` so that a real
          webhook event that landed first (with BoxNow's own message
          id) is never duplicated by the poll.
        * State updates honour the same out-of-order protection as
          ``apply_webhook_event``: ``shipment.parcel_state`` only
          moves forward when the new event's ``event_time`` is
          strictly greater than ``shipment.last_event_at``.
        * Downstream side-effects mirror the webhook path: order
          status cascades via ``_apply_order_status_transition``,
          locker-arrival notification on
          ``final-destination``, delivered notification on
          ``delivered``.

        Returns:
            ``{"parcel_id", "events_synced", "state", "last_event_at"}``
        """
        if not shipment.parcel_id:
            logger.warning(
                "sync_shipment_state: shipment %s has no parcel_id — "
                "cannot poll",
                shipment.pk,
            )
            return {
                "parcel_id": None,
                "events_synced": 0,
                "state": shipment.parcel_state,
                "last_event_at": shipment.last_event_at,
            }

        parcel_id = shipment.parcel_id
        logger.info(
            "sync_shipment_state: polling BoxNow for parcel_id=%s",
            parcel_id,
        )

        response = BoxNowClient().get_parcel_info(parcel_id=parcel_id)
        data_rows: list[dict] = (response or {}).get("data") or []
        if not data_rows:
            logger.warning(
                "sync_shipment_state: BoxNow returned no data for "
                "parcel_id=%s — likely not yet visible or wrong id",
                parcel_id,
            )
            # Still bump last_polled_at so the batch task can move on
            # to next candidates rather than spinning on this one.
            BoxNowShipment.objects.filter(pk=shipment.pk).update(
                last_polled_at=timezone.now()
            )
            return {
                "parcel_id": parcel_id,
                "events_synced": 0,
                "state": shipment.parcel_state,
                "last_event_at": shipment.last_event_at,
            }

        parcel = data_rows[0]
        events: list[dict] = parcel.get("events") or []
        events_synced = 0

        # Apply events in chronological order so the state machine
        # observes transitions the same way the webhook stream would
        # have delivered them (oldest first).
        for event in sorted(events, key=lambda e: e.get("createTime") or ""):
            applied = cls._apply_poll_event(shipment, parcel, event)
            if applied is not None:
                events_synced += 1

        BoxNowShipment.objects.filter(pk=shipment.pk).update(
            last_polled_at=timezone.now()
        )
        shipment.refresh_from_db(
            fields=["parcel_state", "last_event_at", "last_polled_at"]
        )

        logger.info(
            "sync_shipment_state: parcel_id=%s events_synced=%d state=%s",
            parcel_id,
            events_synced,
            shipment.parcel_state,
        )

        return {
            "parcel_id": parcel_id,
            "events_synced": events_synced,
            "state": shipment.parcel_state,
            "last_event_at": shipment.last_event_at,
        }

    @classmethod
    def _apply_poll_event(
        cls,
        shipment: BoxNowShipment,
        parcel: dict,
        event: dict,
    ) -> BoxNowParcelEvent | None:
        """Idempotently insert one poll-derived event and cascade state.

        Returns the new ``BoxNowParcelEvent`` row, or ``None`` if the
        event was already recorded (via webhook or a prior poll).
        """
        raw_event = event.get("type") or ""
        event_time_raw = event.get("createTime")
        if not raw_event or not event_time_raw:
            logger.warning(
                "_apply_poll_event: malformed event for parcel_id=%s — %r",
                shipment.parcel_id,
                event,
            )
            return None

        try:
            mapped_state = BoxNowParcelState.from_webhook_event(raw_event)
        except ValueError:
            logger.warning(
                "_apply_poll_event: unknown BoxNow event %r for "
                "parcel_id=%s — recording as-is",
                raw_event,
                shipment.parcel_id,
            )
            mapped_state = raw_event

        event_time = parse_datetime(event_time_raw)

        with transaction.atomic():
            locked = (
                BoxNowShipment.objects.select_for_update()
                .select_related("order")
                .get(pk=shipment.pk)
            )

            # Two-stage dedup so the poll never duplicates a webhook
            # row that arrived with BoxNow's own CloudEvents id.
            already_recorded = BoxNowParcelEvent.objects.filter(
                shipment=locked,
                event_type=mapped_state,
                event_time=event_time,
            ).exists()
            if already_recorded:
                return None

            synthetic_id = (
                f"{cls._POLL_MESSAGE_ID_PREFIX}:{locked.parcel_id}:"
                f"{mapped_state}:{event_time_raw}"
            )

            new_event, created = BoxNowParcelEvent.objects.get_or_create(
                webhook_message_id=synthetic_id,
                defaults={
                    "shipment": locked,
                    "event_type": mapped_state,
                    "parcel_state": parcel.get("state", ""),
                    "event_time": event_time,
                    "display_name": event.get("locationDisplayName", "") or "",
                    "postal_code": event.get("postalCode", "") or "",
                    "additional_information": "",
                    "raw_payload": {
                        "source": "poll",
                        "event": event,
                        "parcel_summary": {
                            "id": parcel.get("id"),
                            "state": parcel.get("state"),
                            "updateTime": parcel.get("updateTime"),
                        },
                    },
                },
            )

            if not created:
                # Lost a race with a concurrent poll that won the
                # unique-key insert microseconds earlier.
                return None

            if locked.last_event_at is None or (
                event_time is not None and event_time > locked.last_event_at
            ):
                locked.parcel_state = mapped_state
                locked.last_event_at = event_time
                locked.save(
                    update_fields=[
                        "parcel_state",
                        "last_event_at",
                        "updated_at",
                    ]
                )

            cls._apply_order_status_transition(locked, mapped_state)

            if mapped_state == BoxNowParcelState.FINAL_DESTINATION:
                from shipping_boxnow.tasks import (  # noqa: PLC0415
                    boxnow_send_arrival_notification,
                )

                transaction.on_commit(
                    lambda eid=new_event.id: (
                        boxnow_send_arrival_notification.delay(eid)
                    )
                )
            elif mapped_state == BoxNowParcelState.DELIVERED:
                cls._dispatch_delivered_notification(locked)

        return new_event

    # ------------------------------------------------------------------
    # sync_lockers
    # ------------------------------------------------------------------

    @classmethod
    def sync_lockers(cls) -> dict[str, int]:
        """
        Fetch all BoxNow APM lockers from the API and upsert them into
        the local ``BoxNowLocker`` table.

        Designed to be called by the ``sync_boxnow_lockers`` Celery beat
        task on a daily schedule.  Returns a summary dict so the task
        can log the outcome.

        Strategy
        --------
        We collect all ``external_id`` values returned by BoxNow and
        process them in batches of 100 inside their own ``atomic``
        blocks.  After all batches are done, any locker whose
        ``external_id`` is absent from the API response is marked
        ``is_active=False``.

        Returns:
            ``{"created": N, "updated": M, "deactivated": K}``
        """
        logger.info("sync_lockers: fetching all BoxNow APM destinations")
        destinations: list[dict] = BoxNowClient().list_destinations(
            location_type="apm"
        )

        seen_external_ids: set[str] = set()
        created_count = 0
        updated_count = 0
        batch_size = 100

        for batch_start in range(0, len(destinations), batch_size):
            batch = destinations[batch_start : batch_start + batch_size]
            with transaction.atomic():
                for dest in batch:
                    external_id: str = str(dest.get("id", ""))
                    if not external_id:
                        logger.warning(
                            "sync_lockers: destination with no id — "
                            "skipping: %r",
                            dest,
                        )
                        continue

                    seen_external_ids.add(external_id)

                    locker, was_created = BoxNowLocker.objects.update_or_create(
                        external_id=external_id,
                        defaults=cls._locker_defaults_from_dest(dest),
                    )
                    if was_created:
                        created_count += 1
                    else:
                        updated_count += 1

        # --- Deactivate stale lockers ------------------------------------
        deactivated_count = 0
        if seen_external_ids:
            deactivated_count = BoxNowLocker.objects.exclude(
                external_id__in=seen_external_ids
            ).update(is_active=False)
        else:
            # BoxNow returned an empty list — don't deactivate everything;
            # that's almost certainly an API error.  Log a warning and bail.
            logger.warning(
                "sync_lockers: BoxNow returned zero destinations — "
                "skipping deactivation to avoid wiping all lockers"
            )

        logger.info(
            "sync_lockers: created=%d updated=%d deactivated=%d",
            created_count,
            updated_count,
            deactivated_count,
        )
        return {
            "created": created_count,
            "updated": updated_count,
            "deactivated": deactivated_count,
        }

    # ------------------------------------------------------------------
    # fetch_label_bytes
    # ------------------------------------------------------------------

    @classmethod
    def fetch_label_bytes(cls, shipment: BoxNowShipment) -> bytes:
        """
        Return the PDF label bytes for a parcel, using the Django cache
        to avoid repeated downloads.

        Cache key: ``boxnow:label:{parcel_id}``
        TTL:       1 hour (labels don't change once printed).

        Args:
            shipment: The shipment whose label to fetch.

        Returns:
            Raw PDF bytes.

        Raises:
            BoxNowAPIError: The BoxNow API returned an error.
        """
        cache_key = f"boxnow:label:{shipment.parcel_id}"
        cached: bytes | None = cache.get(cache_key)
        if cached is not None:
            return cached

        logger.info(
            "fetch_label_bytes: cache miss for parcel_id=%s — "
            "fetching from BoxNow",
            shipment.parcel_id,
        )
        label_bytes: bytes = BoxNowClient().fetch_parcel_label(
            shipment.parcel_id
        )
        cache.set(cache_key, label_bytes, timeout=_LABEL_CACHE_TTL)
        return label_bytes

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @classmethod
    def _apply_order_status_transition(
        cls,
        shipment: BoxNowShipment,
        mapped_state: BoxNowParcelState | str,
    ) -> None:
        """
        Advance the Order's status based on the BoxNow parcel event.

        Uses ``OrderService.update_order_status`` for all transitions so
        signals and history logging fire correctly.  Swallows
        ``InvalidStatusTransitionError`` with a warning so that an
        out-of-order or duplicate webhook event cannot roll back the
        already-recorded ``BoxNowParcelEvent``.

        Safety rules:
        - Never move a terminal order (DELIVERED, COMPLETED, CANCELED,
          RETURNED, REFUNDED) backwards.
        - Only advance to SHIPPED if the order is still PENDING or
          PROCESSING.
        - Only advance to DELIVERED/CANCELED/RETURNED from non-terminal
          states (or states before the target).

        Args:
            shipment:     The ``BoxNowShipment`` whose parcel event fired
                          (must have ``order`` loaded/loadable).
            mapped_state: The ``BoxNowParcelState`` (or raw string for
                          unknown events).
        """
        # Lazy imports avoid circular dependencies at startup.
        from order.exceptions import InvalidStatusTransitionError
        from order.services import OrderService

        order: Order = shipment.order
        current_status: str = order.status

        # Never touch a terminal order.
        if current_status in _TERMINAL_ORDER_STATUSES:
            logger.info(
                "_apply_order_status_transition: order=%s already in "
                "terminal state %r — skipping",
                order.id,
                current_status,
            )
            return

        new_status: str | None = None

        if mapped_state in _SHIPPED_EVENTS:
            # final-destination / accepted-to-locker → SHIPPED
            if current_status in _PRE_SHIPPED_STATUSES:
                new_status = "SHIPPED"

        elif mapped_state == BoxNowParcelState.DELIVERED:
            new_status = "DELIVERED"

        elif mapped_state in (
            BoxNowParcelState.RETURNED,
            BoxNowParcelState.EXPIRED,
            BoxNowParcelState.MISSING,
            BoxNowParcelState.LOST,
            BoxNowParcelState.ACCEPTED_FOR_RETURN,
        ):
            new_status = "RETURNED"

        elif mapped_state == BoxNowParcelState.CANCELED:
            new_status = "CANCELED"

        # new / in-depot / pending-creation events → no order status change.

        if new_status is None or new_status == current_status:
            return

        # BoxNow can deliver a parcel between two polls / webhook
        # deliveries without us ever observing an intermediate
        # final_destination event (especially if the customer picks
        # up immediately). The order state machine requires
        # PROCESSING → SHIPPED → DELIVERED, so a direct jump is
        # rejected. Walk the missing SHIPPED step first. Mirrors
        # ``AcsService._apply_order_status_transition`` — same gap.
        if (
            new_status == "DELIVERED"
            and current_status in _PRE_SHIPPED_STATUSES
        ):
            try:
                OrderService.update_order_status(order, "SHIPPED")
            except InvalidStatusTransitionError as exc:
                logger.warning(
                    "BoxNow: cannot bridge order=%s through SHIPPED "
                    "before DELIVERED (%r → SHIPPED): %s",
                    order.id,
                    current_status,
                    exc,
                )
                return

        logger.info(
            "_apply_order_status_transition: order=%s %r → %r "
            "(boxnow_event=%r)",
            order.id,
            current_status,
            new_status,
            mapped_state,
        )
        try:
            OrderService.update_order_status(order, new_status)
        except InvalidStatusTransitionError as exc:
            logger.warning(
                "_apply_order_status_transition: invalid transition for "
                "order=%s (%r → %r): %s — ignoring",
                order.id,
                current_status,
                new_status,
                exc,
            )
            return

        # BoxNow COD is paid at the locker: the customer must pay at
        # the APM terminal before the compartment opens, so a parcel
        # can only reach ``delivered`` after the money was collected.
        # (Unlike ACS, where the courier remits later and the nightly
        # COD reconcile confirms the payout — BoxNow's API has no
        # payout endpoint, and none is needed.)
        #
        # Then mirror AcsService: auto-complete paid orders when the
        # carrier hands them DELIVERED. ``silent_for_customer=True``
        # because the customer just got the DELIVERED notifications in
        # this same transition.
        if new_status == "DELIVERED":
            if shipment.payment_mode == BoxNowPaymentMode.COD:
                cls._mark_cod_order_paid_on_delivery(shipment)
            OrderService.maybe_advance_to_completed(
                order, silent_for_customer=True
            )

    @classmethod
    def _mark_cod_order_paid_on_delivery(cls, shipment: BoxNowShipment) -> None:
        """Flip a COD order's payment from PENDING to COMPLETED.

        BoxNow collects COD at the locker terminal *before* the
        compartment opens, so a ``delivered`` parcel event is proof of
        payment — no later remittance-reconcile step exists (or is
        offered by the BoxNow API). Mirrors
        ``AcsService._mark_cod_order_paid_if_pending``: idempotent on
        ``payment_status == PENDING`` and fires ``order_paid`` so the
        Meta CAPI Purchase dispatch runs (COD orders are past the
        PENDING → PROCESSING transition that normally emits it).
        """
        from order.enum.status import PaymentStatus
        from order.signals import order_paid

        order = shipment.order
        if order.payment_status != PaymentStatus.PENDING:
            return
        order.mark_as_paid(payment_method="boxnow_cod")
        logger.info(
            "BoxNow COD delivered: order=%s payment_status PENDING -> "
            "COMPLETED (parcel=%s)",
            order.id,
            shipment.parcel_id,
        )
        order_paid.send(sender=type(order), order=order)

    @classmethod
    def _advance_pending_order_to_processing(cls, order: Order) -> None:
        """Bump a PENDING order to PROCESSING after voucher mint.

        See AcsService._advance_pending_order_to_processing for the
        rationale — same UX guarantee for COD shoppers on the BoxNow
        path.
        """
        from order.exceptions import InvalidStatusTransitionError
        from order.models.order import Order as _Order
        from order.services import OrderService

        current_status = (
            _Order.objects.filter(pk=order.pk)
            .values_list("status", flat=True)
            .first()
        )
        if current_status != "PENDING":
            return
        order.status = current_status
        try:
            OrderService.update_order_status(order, "PROCESSING")
        except InvalidStatusTransitionError as exc:
            logger.warning(
                "BoxNow voucher mint: could not advance order=%s PENDING -> "
                "PROCESSING: %s",
                order.id,
                exc,
            )

    @staticmethod
    def _locker_defaults_from_dest(dest: dict) -> dict:
        """
        Build the ``defaults`` dict for ``BoxNowLocker.update_or_create``
        from a raw BoxNow destination object.

        BoxNow field names use camelCase; we map them to our snake_case
        columns here so ``sync_lockers`` stays readable.
        """
        from shipping_boxnow.enum import BoxNowLockerType  # noqa: PLC0415

        raw_type = dest.get("locationType", "apm")
        try:
            locker_type = BoxNowLockerType(raw_type)
        except ValueError:
            locker_type = BoxNowLockerType.APM

        # BoxNow returns the address fields at the **top level** of the
        # destination object (``addressLine1`` / ``postalCode`` /
        # ``country``), not nested under an ``address`` sub-object. The
        # previous version of this method walked a non-existent
        # ``dest["address"]`` dict, so every synced locker landed in
        # the DB with empty address fields — silently breaking
        # postal-code filtering and the admin display.
        return {
            "type": locker_type,
            "image_url": dest.get("imageUrl") or None,
            "lat": dest.get("lat", 0),
            "lng": dest.get("lng", 0),
            "title": dest.get("title", ""),
            "name": dest.get("name", ""),
            "address_line_1": dest.get("addressLine1", ""),
            "address_line_2": dest.get("addressLine2", ""),
            "postal_code": dest.get("postalCode", ""),
            "country_code": dest.get("country") or "GR",
            "note": dest.get("note", ""),
            "is_active": True,
            "last_synced_at": timezone.now(),
        }
