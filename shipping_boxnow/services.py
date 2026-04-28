from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from shipping_boxnow.client import BoxNowClient
from shipping_boxnow.enum import BoxNowParcelState
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

    @classmethod
    @transaction.atomic
    def create_shipment_for_order(cls, order: Order) -> BoxNowShipment:
        """
        Call BoxNow's delivery-request API and persist the IDs on the
        shipment row.

        Why atomic + select_for_update
        --------------------------------
        ``handle_payment_succeeded`` can fire multiple times (Stripe /
        Viva webhooks both retry on network timeouts).  Locking the row
        prevents two Celery task invocations from both seeing a blank
        ``delivery_request_id`` and issuing duplicate delivery requests
        to BoxNow.

        Idempotency
        -----------
        If ``shipment.delivery_request_id`` is already set the method
        returns the existing shipment unchanged.  BoxNow errors P410
        (duplicate order number) would otherwise bubble up and trigger
        unnecessary retries.

        Args:
            order: The paid Order instance.  Must have an associated
                   ``BoxNowShipment`` row (created at order-creation
                   time by ``OrderService.create_order``).

        Returns:
            The ``BoxNowShipment`` instance (created or pre-existing).

        Raises:
            ValueError: The order has no ``BoxNowShipment`` row.
            BoxNowAPIError: The BoxNow API returned a non-retryable error.
            BoxNowRetryableError: Transient API failure — Celery will retry.
        """
        # Lock the shipment row for the duration of this transaction.
        try:
            shipment: BoxNowShipment = (
                BoxNowShipment.objects.select_for_update()
                .select_related("order")
                .get(order=order)
            )
        except BoxNowShipment.DoesNotExist:
            raise ValueError(
                f"Order {order.id} has no BoxNow shipment row. "
                "Create a BoxNowShipment at order-creation time."
            )

        # --- Idempotency guard -------------------------------------------
        if shipment.delivery_request_id:
            logger.info(
                "create_shipment_for_order: order=%s already has "
                "delivery_request_id=%s — returning existing shipment",
                order.id,
                shipment.delivery_request_id,
            )
            return shipment

        # --- Build the BoxNow delivery-request payload -------------------
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

        payload: dict[str, Any] = {
            "orderNumber": str(order.id),
            "invoiceValue": invoice_value,
            "paymentMode": shipment.payment_mode,
            "amountToBeCollected": str(shipment.amount_to_be_collected.amount),
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
                    "weight": shipment.weight_grams or 0,
                }
            ],
        }

        # --- Call BoxNow API ---------------------------------------------
        logger.info(
            "create_shipment_for_order: creating delivery request "
            "for order=%s locker=%s",
            order.id,
            shipment.locker_external_id,
        )
        try:
            response = BoxNowClient().create_delivery_request(payload)
        except BoxNowAPIError as exc:
            # Non-retryable business error (e.g. P410 duplicate order number,
            # P402 invalid locker ID). Persist diagnostics so ops can inspect.
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
            shipment.metadata = {
                **shipment.metadata,
                "last_error": {
                    "type": "BoxNowAPIError",
                    "code": exc.code,
                    "message": exc.message,
                    "status_code": exc.status_code,
                    "response_text": exc.response_text[:500],
                },
            }
            shipment.save(update_fields=["metadata", "updated_at"])
            raise

        delivery_request_id: str = response["id"]
        parcel_id: str = response["parcels"][0]["id"]

        # --- Persist BoxNow IDs on the shipment --------------------------
        shipment.delivery_request_id = delivery_request_id
        shipment.parcel_id = parcel_id
        shipment.parcel_state = BoxNowParcelState.NEW
        shipment.last_event_at = timezone.now()
        shipment.metadata = {
            **shipment.metadata,
            "create_response": response,
            "last_error": None,
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

        # --- Update Order tracking fields ---------------------------------
        # ``add_tracking_info`` issues its own save() with update_fields;
        # that save is covered by our enclosing atomic block.
        order.add_tracking_info(
            tracking_number=parcel_id,
            shipping_carrier="boxnow",
        )

        logger.info(
            "create_shipment_for_order: order=%s → "
            "delivery_request_id=%s parcel_id=%s",
            order.id,
            delivery_request_id,
            parcel_id,
        )
        return shipment

    # ------------------------------------------------------------------
    # cancel_shipment
    # ------------------------------------------------------------------

    @classmethod
    @transaction.atomic
    def cancel_shipment(
        cls, shipment: BoxNowShipment, *, reason: str = ""
    ) -> None:
        """
        Cancel a BoxNow parcel delivery.

        BoxNow only permits cancellation while the parcel is in the
        ``NEW`` state (error P420 otherwise).  We mirror that guard
        locally so we never make an API call that BoxNow would reject,
        and so the caller gets a clear error message.

        Args:
            shipment: The ``BoxNowShipment`` to cancel.
            reason:   Human-readable reason for the cancellation
                      (stored in ``shipment.metadata["cancellations"]``).

        Raises:
            BoxNowAPIError: Shipment is not in the ``NEW`` state (P420).
            BoxNowAPIError: BoxNow API rejected the request.
        """
        # Re-acquire the row with a lock so no concurrent process
        # (e.g. a webhook handler) changes the state underneath us.
        locked: BoxNowShipment = BoxNowShipment.objects.select_for_update().get(
            pk=shipment.pk
        )

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

        logger.info(
            "cancel_shipment: cancelling parcel_id=%s reason=%r",
            locked.parcel_id,
            reason,
        )

        BoxNowClient().cancel_parcel(locked.parcel_id)

        # Record the cancellation timestamp and state.
        locked.cancel_requested_at = timezone.now()
        locked.parcel_state = BoxNowParcelState.CANCELED

        # Append the reason to the cancellations audit list.
        cancellations: list[dict] = locked.metadata.get("cancellations", [])
        cancellations.append(
            {
                "reason": reason,
                "cancelled_at": locked.cancel_requested_at.isoformat(),
            }
        )
        locked.metadata = {**locked.metadata, "cancellations": cancellations}

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
            locked.parcel_id,
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

        # --- Locate shipment ---------------------------------------------
        parcel_id: str = data["parcelId"]
        shipment: BoxNowShipment | None = (
            BoxNowShipment.objects.select_for_update()
            .filter(parcel_id=parcel_id)
            .first()
        )
        if shipment is None:
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

        # --- Atomic block: create event + update shipment + order --------
        with transaction.atomic():
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
            order: Order = shipment.order
            cls._apply_order_status_transition(order, mapped_state)

            # --- Enqueue arrival notification for final-destination -------
            if mapped_state == BoxNowParcelState.FINAL_DESTINATION:
                try:
                    from shipping_boxnow.tasks import (  # noqa: PLC0415
                        boxnow_send_arrival_notification,
                    )

                    boxnow_send_arrival_notification.delay(event.id)
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
                notification_type="BOXNOW_PARCEL_DELIVERED",
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
        cls, order: Order, mapped_state: BoxNowParcelState | str
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
            order:        The ``Order`` linked to the shipment.
            mapped_state: The ``BoxNowParcelState`` (or raw string for
                          unknown events).
        """
        # Lazy imports avoid circular dependencies at startup.
        from order.exceptions import InvalidStatusTransitionError
        from order.services import OrderService

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

        address: dict = dest.get("address", {})

        return {
            "type": locker_type,
            "image_url": dest.get("imageUrl") or None,
            "lat": dest.get("lat", 0),
            "lng": dest.get("lng", 0),
            "title": dest.get("title", ""),
            "name": dest.get("name", ""),
            "address_line_1": address.get("addressLine1", ""),
            "address_line_2": address.get("addressLine2", ""),
            "postal_code": address.get("postalCode", ""),
            "country_code": address.get("countryCode", "GR"),
            "note": dest.get("note", ""),
            "is_active": True,
            "last_synced_at": timezone.now(),
        }
