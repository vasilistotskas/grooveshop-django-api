"""Application-level orchestration for ACS deliveries.

All public methods are classmethods so callers never instantiate the
service.  Concurrency model mirrors :class:`shipping_boxnow.services.\
BoxNowService`: any state-changing method takes a ``select_for_update``
lock on the shipment row before mutating, so simultaneous Celery task
runs and admin actions never race.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from shipping.enum import ShippingKind
from shipping_acs.client import AcsClient
from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.exceptions import AcsAPIError
from shipping_acs.models import (
    AcsPickupList,
    AcsShipment,
    AcsStation,
    AcsTrackingEvent,
)

if TYPE_CHECKING:
    from order.models.order import Order

logger = logging.getLogger(__name__)


# State-transition tables (string literals to avoid importing
# order.enum.status at module import time and risking a circular
# import chain since order/services.py imports shipping providers).

_SHIPPED_STATES: frozenset[AcsShipmentState] = frozenset(
    {
        AcsShipmentState.IN_TRANSIT,
        AcsShipmentState.AT_DESTINATION,
        AcsShipmentState.OUT_FOR_DELIVERY,
    }
)
_PRE_SHIPPED_ORDER_STATUSES: frozenset[str] = frozenset(
    {"PENDING", "PROCESSING"}
)
_TERMINAL_ORDER_STATUSES: frozenset[str] = frozenset(
    {"DELIVERED", "COMPLETED", "CANCELED", "RETURNED", "REFUNDED"}
)

_LABEL_CACHE_TTL = 3600  # 1 hour, mirrors BoxNow


# Minimum chargeable weight per ACS_Create_Voucher Weight rules
# (PDF: ``min ή 0.5``).  Below this the API rejects the voucher.
_MIN_VOUCHER_KG = Decimal("0.5")
# Defensive upper bound; ACS does not document a hard maximum but the
# parcels we ship rarely exceed 30 kg and clamping prevents a bad
# product weight from poisoning the create-voucher call.
_MAX_VOUCHER_KG = Decimal("999")


def _kg_from_grams(weight_grams: int | None) -> str:
    """Convert internal grams → ACS's kilogram-decimal payload value.

    Returns a Greek-locale comma-decimal string (e.g. ``"0,5"``).
    ACS parses numeric strings with the Greek convention — dot is the
    thousands separator and comma is the decimal — so ``"0.5"`` is
    read as ``5`` KG and a single small parcel ends up billed at the
    5 KG tariff. Sending the comma form keeps the kg value intact.

    Values below the 0.5 kg minimum are clamped up to 0.5 (per PDF:
    "min ή 0.5"); values above the safety ceiling are clamped down
    with a warning so a single bad row can't dead-letter the
    create-voucher Celery task fan-out.
    """
    grams = int(weight_grams or 0)
    if grams <= 0:
        kg = _MIN_VOUCHER_KG
    else:
        kg = (Decimal(grams) / Decimal(1000)).quantize(Decimal("0.001"))
        if kg < _MIN_VOUCHER_KG:
            kg = _MIN_VOUCHER_KG
        if kg > _MAX_VOUCHER_KG:
            logger.warning(
                "Clamping ACS voucher weight from %s kg to %s kg — "
                "likely a unit mix-up upstream.",
                kg,
                _MAX_VOUCHER_KG,
            )
            kg = _MAX_VOUCHER_KG

    # Trim trailing zeros (12.000 → 12.0) then swap dot → comma for
    # Greek locale parsing on the ACS side.
    text = f"{kg.normalize():f}"
    if "." not in text:
        text = f"{text}.0"
    return text.replace(".", ",")


def _event_fingerprint(
    *, shipment_id: int, event_time: str, action: str, location: str
) -> str:
    """Stable idempotency key for ``AcsTrackingEvent``.

    SHA-1 of the four fields most likely to uniquely identify an
    ACS_TrackingDetails row.  Replaces BoxNow's webhook_message_id
    since ACS doesn't issue per-event IDs.
    """
    blob = f"{shipment_id}|{event_time}|{action}|{location}"
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


class AcsService:
    """Application-level orchestration for ACS deliveries."""

    # ------------------------------------------------------------------
    # Voucher creation
    # ------------------------------------------------------------------

    # How long another Celery worker waits before re-attempting after
    # picking up a shipment that another instance has already started
    # minting. Larger than the ACS HTTP timeout (15s) plus urllib3
    # retry budget so a healthy mint always wins; smaller than Celery's
    # task timeout so a crashed worker doesn't permanently strand a
    # shipment.
    _MINT_CLAIM_TTL_SECONDS = 90

    @classmethod
    def create_voucher_for_order(cls, order: Order) -> AcsShipment:
        """Issue a voucher for ``order`` via ``ACS_Create_Voucher``.

        Three-phase design that survives a connection-level failure
        between the API success and the local save:

        1. **Claim** — short atomic block: lock the shipment row,
           short-circuit on existing ``voucher_no``, sync ``cod_amount``,
           and stamp ``metadata['mint_started_at']``. The claim
           prevents two concurrent Celery workers from both POSTing
           ``ACS_Create_Voucher`` and minting duplicate vouchers.
        2. **API call** — no DB lock, no open transaction. Network
           latency or `idle_in_transaction_session_timeout` can no
           longer roll back the local save.
        3. **Persist** — fresh atomic + ``select_for_update`` race
           check. If another worker has already saved a voucher (the
           claim TTL expired during a slow API), our voucher becomes
           the duplicate; we attempt ``ACS_Delete_Voucher`` and return
           the row the other worker saved.
        """
        from shipping_acs.enum.charge_type import AcsChargeType

        client = AcsClient()
        # ----- Phase 1: claim the row + sync COD amount -----
        with transaction.atomic():
            try:
                shipment: AcsShipment = (
                    AcsShipment.objects.select_for_update()
                    .select_related("order")
                    .get(order=order)
                )
            except AcsShipment.DoesNotExist as exc:
                raise ValueError(
                    f"Order {order.id} has no AcsShipment row. Create one "
                    "at order-creation time."
                ) from exc

            if shipment.voucher_no:
                logger.info(
                    "create_voucher_for_order: order=%s already has "
                    "voucher_no=%s — returning existing shipment",
                    order.id,
                    shipment.voucher_no,
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
                        # Another worker is in the middle of minting.
                        # Bail out as a retryable so Celery backs off
                        # rather than racing in.
                        from shipping_acs.exceptions import AcsRetryableError

                        raise AcsRetryableError(
                            alias="ACS_Create_Voucher",
                            error_message=(
                                f"Voucher mint already in progress for "
                                f"order={order.id} (started {age:.1f}s ago)."
                            ),
                        )

            update_fields = ["metadata"]
            metadata["mint_started_at"] = timezone.now().isoformat()
            shipment.metadata = metadata

            cod_synced = (
                shipment.charge_type == AcsChargeType.COD
                and (not shipment.cod_amount or shipment.cod_amount.amount == 0)
                and order.paid_amount
                and order.paid_amount.amount > 0
            )
            if cod_synced:
                shipment.cod_amount = order.paid_amount
                update_fields.extend(["cod_amount", "cod_amount_currency"])

            shipment.save(update_fields=update_fields)

        # ----- Phase 2: API call (no DB lock held) -----
        params = cls._build_create_voucher_params(order, shipment, client)
        try:
            result = client.create_voucher(params)
        except Exception:
            # API call failed — release the claim so a future retry
            # can proceed without waiting out the TTL.
            cls._release_mint_claim(shipment)
            raise

        voucher_no = (result.get("Voucher_No") or "").strip()
        if not voucher_no:
            cls._release_mint_claim(shipment)
            error_message = (
                result.get("Error_Message")
                or "ACS_Create_Voucher succeeded but returned no Voucher_No."
            )
            raise AcsAPIError(
                alias="ACS_Create_Voucher",
                error_message=error_message,
                raw=result,
            )

        children: list[str] = []
        if shipment.item_quantity > 1:
            try:
                children = client.get_multipart_vouchers(voucher_no)
            except AcsAPIError as exc:
                logger.warning(
                    "ACS_Get_Multipart_Vouchers failed for voucher %s: %s",
                    voucher_no,
                    exc,
                )

        # ----- Phase 3: persist (fresh atomic, race-checked) -----
        with transaction.atomic():
            shipment = (
                AcsShipment.objects.select_for_update()
                .select_related("order")
                .get(pk=shipment.pk)
            )
            if shipment.voucher_no and shipment.voucher_no != voucher_no:
                # Another worker won the race. Our voucher is the dupe.
                logger.warning(
                    "Concurrent voucher mint for order=%s: DB saved %s, "
                    "we minted %s — attempting cancel.",
                    order.id,
                    shipment.voucher_no,
                    voucher_no,
                )
                try:
                    client.delete_voucher(voucher_no)
                except AcsAPIError as exc:
                    logger.error(
                        "Failed to cancel duplicate voucher %s for order=%s: %s "
                        "— flag in shipment.metadata['orphan_vouchers'] for "
                        "manual reconciliation.",
                        voucher_no,
                        order.id,
                        exc,
                    )
                    metadata = shipment.metadata or {}
                    orphans = list(metadata.get("orphan_vouchers") or [])
                    orphans.append(voucher_no)
                    metadata["orphan_vouchers"] = orphans
                    shipment.metadata = metadata
                    shipment.save(update_fields=["metadata"])
                return shipment

            metadata = shipment.metadata or {}
            metadata.pop("mint_started_at", None)
            metadata["create_response"] = result
            metadata["multipart_vouchers"] = children

            shipment.voucher_no = voucher_no
            shipment.shipment_state = AcsShipmentState.NEW
            shipment.metadata = metadata
            shipment.save(
                update_fields=["voucher_no", "shipment_state", "metadata"]
            )

        order.add_tracking_info(voucher_no, "acs")
        return shipment

    @classmethod
    def _release_mint_claim(cls, shipment: AcsShipment) -> None:
        """Drop the ``mint_started_at`` flag so a retry can proceed.

        Best effort — a failure here just means the next retry has to
        wait for the TTL to expire.
        """
        try:
            with transaction.atomic():
                fresh = (
                    AcsShipment.objects.select_for_update()
                    .only("metadata")
                    .get(pk=shipment.pk)
                )
                metadata = fresh.metadata or {}
                if metadata.pop("mint_started_at", None) is not None:
                    fresh.metadata = metadata
                    fresh.save(update_fields=["metadata"])
        except Exception:
            logger.warning(
                "Failed to release mint claim for shipment=%s — next retry "
                "will wait out the %ss TTL.",
                shipment.pk,
                cls._MINT_CLAIM_TTL_SECONDS,
                exc_info=True,
            )

    @classmethod
    def _build_create_voucher_params(
        cls,
        order: Order,
        shipment: AcsShipment,
        client: AcsClient,
    ) -> dict[str, Any]:
        """Translate Order + AcsShipment fields to the ACS payload."""
        sender = getattr(settings, "SITE_NAME", "GrooveShop")
        country_code = (
            order.country_id
            if isinstance(order.country_id, str)
            else (order.country.alpha_2 if order.country_id else "GR")
        )

        params: dict[str, Any] = {
            "Billing_Code": client.billing_code,
            "Pickup_Date": timezone.localdate().isoformat(),
            "Sender": sender,
            "Recipient_Name": (
                f"{order.first_name} {order.last_name}".strip() or order.email
            ),
            "Recipient_Address": order.street,
            "Recipient_Address_Number": order.street_number,
            "Recipient_Zipcode": order.zipcode,
            "Recipient_Region": order.city,
            "Recipient_Phone": str(order.phone) if order.phone else "",
            "Recipient_Cell_Phone": str(order.phone) if order.phone else "",
            "Recipient_Country": country_code or "GR",
            "Recipient_Email": order.email or "",
            "Charge_Type": shipment.charge_type,
            "Item_Quantity": shipment.item_quantity,
            "Weight": _kg_from_grams(shipment.weight_grams),
            "Reference_Key1": str(order.id),
            "Reference_Key2": str(order.uuid),
            "Language": "GR",
        }

        if shipment.delivery_kind == ShippingKind.PICKUP_POINT:
            params["Acs_Station_Destination"] = (
                shipment.station_destination_external_id or ""
            )
            params["Acs_Station_Branch_Destination"] = (
                shipment.station_branch_destination or ""
            )

        if shipment.cod_amount and shipment.cod_amount.amount > 0:
            # ACS parses Cod_Ammount with the Greek locale: dot is the
            # thousands separator, comma is the decimal separator. So
            # "47.01" reads as 4701, which trips the 1500€ cash cap. Send
            # it as a comma-decimal string.
            cod_decimal = shipment.cod_amount.amount.quantize(Decimal("0.01"))
            params["Cod_Ammount"] = format(cod_decimal, "f").replace(".", ",")
            params["Cod_Payment_Way"] = shipment.cod_payment_way or 0

        if shipment.delivery_products:
            params["Acs_Delivery_Products"] = shipment.delivery_products

        return params

    # ------------------------------------------------------------------
    # Voucher cancellation
    # ------------------------------------------------------------------

    @classmethod
    @transaction.atomic
    def cancel_voucher(
        cls,
        shipment: AcsShipment,
        *,
        reason: str = "",
    ) -> None:
        """Cancel a voucher when allowed by ACS rules.

        Per PDF: ``ACS_Delete_Voucher`` only succeeds for vouchers not
        yet finalised in a pickup list — once issued, the voucher is
        immutable.  Caller (admin endpoint) must pass a fresh row.
        """
        shipment = (
            AcsShipment.objects.select_for_update()
            .select_related("order")
            .get(pk=shipment.pk)
        )

        if shipment.shipment_state == AcsShipmentState.CANCELED:
            return  # idempotent

        if shipment.pickup_list_id is not None:
            raise AcsAPIError(
                alias="ACS_Delete_Voucher",
                error_message=(
                    "Cannot cancel a voucher that is already in a "
                    "pickup list — contact ACS support to issue a "
                    "manual return instead."
                ),
            )

        if not shipment.voucher_no:
            shipment.shipment_state = AcsShipmentState.CANCELED
            shipment.cancel_requested_at = timezone.now()
            shipment.save(
                update_fields=["shipment_state", "cancel_requested_at"]
            )
            return

        AcsClient().delete_voucher(shipment.voucher_no)

        shipment.shipment_state = AcsShipmentState.CANCELED
        shipment.cancel_requested_at = timezone.now()
        shipment.metadata = {
            **(shipment.metadata or {}),
            "cancel_reason": reason,
        }
        shipment.save(
            update_fields=[
                "shipment_state",
                "cancel_requested_at",
                "metadata",
            ]
        )

    # ------------------------------------------------------------------
    # Daily pickup list (manifest)
    # ------------------------------------------------------------------

    @classmethod
    @transaction.atomic
    def issue_daily_pickup_list(
        cls,
        *,
        pickup_date: date | None = None,
        billing_code: str | None = None,
        issued_by_id: int | None = None,
    ) -> AcsPickupList | None:
        """Finalise the day's vouchers via ``ACS_Issue_Pickup_List``.

        Returns the created :class:`AcsPickupList` or ``None`` when no
        candidate shipments are eligible (idempotent on re-run).
        """
        the_date = pickup_date or timezone.localdate()

        candidates = list(
            AcsShipment.objects.select_for_update()
            .filter(
                voucher_no__isnull=False,
                pickup_list__isnull=True,
                shipment_state=AcsShipmentState.NEW,
            )
            .values_list("id", flat=True)
        )
        if not candidates:
            logger.info(
                "issue_daily_pickup_list: no candidate shipments for %s",
                the_date,
            )
            return None

        client = AcsClient()
        result = client.issue_pickup_list(pickup_date=the_date.isoformat())

        pickup_list_no = (result.get("PickupList_No") or "").strip()
        if not pickup_list_no:
            unprinted = result.get("Unprinted_Found")
            if unprinted:
                # ACS partial-issue path — surface the error message
                # so admins can fix the offending vouchers.
                raise AcsAPIError(
                    alias="ACS_Issue_Pickup_List",
                    error_message=(
                        result.get("Error_Message")
                        or "ACS_Issue_Pickup_List rejected unprinted vouchers."
                    ),
                    raw=result,
                )
            logger.info(
                "ACS_Issue_Pickup_List returned no PickupList_No for "
                "date=%s — assuming nothing to issue.",
                the_date,
            )
            return None

        billing = billing_code or getattr(settings, "ACS_BILLING_CODE", "")
        pickup_list = AcsPickupList.objects.create(
            pickup_list_no=pickup_list_no,
            issued_at=timezone.now(),
            issued_by_id=issued_by_id,
            billing_code=billing,
            voucher_count=len(candidates),
            metadata={"issue_response": result},
        )

        AcsShipment.objects.filter(id__in=candidates).update(
            pickup_list=pickup_list
        )

        return pickup_list

    # ------------------------------------------------------------------
    # Tracking poll
    # ------------------------------------------------------------------

    @classmethod
    @transaction.atomic
    def poll_shipment_tracking(cls, shipment: AcsShipment) -> AcsShipment:
        """Refresh tracking state + events for ``shipment``.

        Calls ``ACS_Trackingsummary`` for the snapshot and
        ``ACS_TrackingDetails`` for the history; idempotent via
        ``event_fingerprint``.  Always updates ``last_polled_at``;
        only updates ``shipment_state`` on forward transitions.
        """
        shipment = (
            AcsShipment.objects.select_for_update()
            .select_related("order")
            .get(pk=shipment.pk)
        )
        if not shipment.voucher_no:
            shipment.last_polled_at = timezone.now()
            shipment.save(update_fields=["last_polled_at"])
            return shipment

        client = AcsClient()
        summary = client.tracking_summary(shipment.voucher_no)
        details = client.tracking_details(shipment.voucher_no)

        old_state = AcsShipmentState(shipment.shipment_state)
        new_state = AcsShipmentState.from_tracking_summary(
            summary, current=old_state
        )

        # Event upsert via fingerprint
        latest_event_at: datetime | None = shipment.last_event_at
        for row in details:
            event_time_raw = row.get("checkpoint_date_time")
            event_time = parse_datetime(event_time_raw or "")
            if event_time is None:
                continue
            if event_time.tzinfo is None:
                event_time = timezone.make_aware(event_time)

            action = (row.get("checkpoint_action") or "").strip()
            location = (row.get("checkpoint_location") or "").strip()
            fingerprint = _event_fingerprint(
                shipment_id=shipment.id,
                event_time=event_time.isoformat(),
                action=action,
                location=location,
            )
            AcsTrackingEvent.objects.update_or_create(
                event_fingerprint=fingerprint,
                defaults={
                    "shipment": shipment,
                    "event_time": event_time,
                    "checkpoint_action": action[:255],
                    "checkpoint_location": location[:255],
                    "notes": (row.get("checkpoint_notes") or "")[:5000],
                    "raw_payload": row,
                },
            )
            if latest_event_at is None or event_time > latest_event_at:
                latest_event_at = event_time

        delivery_date = parse_datetime(summary.get("delivery_date") or "")
        if delivery_date and delivery_date.tzinfo is None:
            delivery_date = timezone.make_aware(delivery_date)

        update_fields = ["last_polled_at"]
        shipment.last_polled_at = timezone.now()
        if latest_event_at and latest_event_at != shipment.last_event_at:
            shipment.last_event_at = latest_event_at
            update_fields.append("last_event_at")
        if delivery_date and shipment.delivery_date != delivery_date:
            shipment.delivery_date = delivery_date
            update_fields.append("delivery_date")
        if str(summary.get("delivery_flag", "")) != shipment.delivery_flag:
            shipment.delivery_flag = str(summary.get("delivery_flag", ""))[:4]
            update_fields.append("delivery_flag")
        if str(summary.get("returned_flag", "")) != shipment.returned_flag:
            shipment.returned_flag = str(summary.get("returned_flag", ""))[:4]
            update_fields.append("returned_flag")
        raw_status = str(summary.get("shipment_status", ""))[:8]
        if raw_status != shipment.raw_shipment_status:
            shipment.raw_shipment_status = raw_status
            update_fields.append("raw_shipment_status")

        if new_state != old_state:
            shipment.shipment_state = new_state
            update_fields.append("shipment_state")

        shipment.save(update_fields=update_fields)

        if new_state != old_state:
            cls._apply_order_status_transition(shipment.order, new_state)
            cls._maybe_notify_arrival(shipment, new_state, old_state)

        return shipment

    # ------------------------------------------------------------------
    # Stations sync (Phase 2)
    # ------------------------------------------------------------------

    @classmethod
    def sync_stations(
        cls,
        *,
        country: str = "GR",
        kinds: tuple[int, ...] = (1, 7, 8),
    ) -> dict[str, int]:
        """Refresh ``AcsStation`` from the ``Acs_Stations`` endpoint.

        Mirrors :meth:`shipping_boxnow.services.BoxNowService.sync_lockers`,
        including the safety guard: when the API returns zero rows for
        a kind we *do not* deactivate existing rows for that kind —
        a transient API failure must not blank the entire local cache.
        """
        client = AcsClient()
        seen_ids: set[str] = set()
        upserted = 0

        for kind in kinds:
            rows = client.stations(country=country, shop_kind=kind)
            if not rows:
                logger.warning(
                    "Acs_Stations returned zero rows for kind=%s — "
                    "skipping deactivation to avoid wiping the cache.",
                    kind,
                )
                continue

            for row in rows:
                external_id = (
                    row.get("ACS_SHOP_STATION_ID_EN")
                    or row.get("ACS_SHOP_STATION_ID")
                    or ""
                )
                if not external_id:
                    continue
                AcsStation.objects.update_or_create(
                    external_id=external_id,
                    defaults={
                        "branch_code": str(row.get("ACS_SHOP_BRANCH_ID", "")),
                        "shop_kind": kind,
                        "name": (row.get("ACS_SHOP_STATION_DESCR") or "")[:255],
                        "address_line_1": (row.get("ACS_SHOP_ADDRESS") or "")[
                            :255
                        ],
                        "city": (row.get("ACS_SHOP_AREA_DESCR") or "")[:120],
                        "postal_code": (row.get("ACS_SHOP_ZIPCODE") or "")[:20],
                        "country_code": country,
                        "phone": (row.get("ACS_SHOP_PHONES") or "")[:120],
                        "working_hours": (
                            row.get("ACS_SHOP_WORKING_HOURS") or ""
                        ),
                        "lat": _to_decimal(row.get("ACS_SHOP_LAT")),
                        "lng": _to_decimal(row.get("ACS_SHOP_LONG")),
                        "is_active": True,
                        "last_synced_at": timezone.now(),
                    },
                )
                seen_ids.add(external_id)
                upserted += 1

        # Deactivate stations not seen in this run (only when at least
        # one of the polled kinds returned data — handled by the
        # `continue` above).
        deactivated = 0
        if seen_ids:
            deactivated = (
                AcsStation.objects.filter(country_code=country)
                .exclude(external_id__in=seen_ids)
                .update(is_active=False)
            )

        return {"upserted": upserted, "deactivated": deactivated}

    # ------------------------------------------------------------------
    # Label / PDF helpers
    # ------------------------------------------------------------------

    @classmethod
    def fetch_label_bytes(cls, shipment: AcsShipment) -> bytes:
        """Return the PDF label bytes for ``shipment``, cached for 1h."""
        if not shipment.voucher_no:
            raise AcsAPIError(
                alias="ACS_Print_Voucher",
                error_message=(
                    "Cannot fetch a label before the voucher is created."
                ),
            )
        cache_key = f"acs:label:{shipment.voucher_no}"
        cached = cache.get(cache_key)
        if cached:
            return cached

        pdf = AcsClient().print_voucher(shipment.voucher_no)
        cache.set(cache_key, pdf, timeout=_LABEL_CACHE_TTL)
        return pdf

    @classmethod
    def fetch_pickup_list_pdf(cls, pickup_list: AcsPickupList) -> bytes:
        """Return the manifest PDF for ``pickup_list``, cached for 1h."""
        cache_key = f"acs:pickup_list:{pickup_list.pickup_list_no}"
        cached = cache.get(cache_key)
        if cached:
            return cached
        pdf = AcsClient().print_pickup_list(
            mass_number=pickup_list.pickup_list_no,
            pickup_date=pickup_list.issued_at.date().isoformat(),
        )
        cache.set(cache_key, pdf, timeout=_LABEL_CACHE_TTL)
        return pdf

    @classmethod
    def fetch_pod(cls, shipment: AcsShipment) -> bytes:
        """Return the proof-of-delivery PDF for ``shipment``."""
        return AcsClient().pod_from_reference_no(str(shipment.order_id))

    # ------------------------------------------------------------------
    # COD payouts reconciliation (Phase 4c)
    # ------------------------------------------------------------------

    @classmethod
    def reconcile_cod_payouts(
        cls,
        *,
        cod_payment_date: date | None = None,
        user_locals: str = "GR",
    ) -> dict[str, int]:
        """Mirror ACS_COD_Beneficiary_Info into AcsCodPayout rows.

        Idempotent: re-runs upsert by ``(voucher_no, cod_payment_date)``.
        Each row is linked back to its ``AcsShipment`` when the
        matching voucher number is found locally — accounting can then
        join through to the originating ``Order`` for reconciliation.

        Returns a counters dict for the Celery task to log.
        """
        # Lazy import — keeps service-module import cheap when COD is
        # not used and avoids forcing the full Money/decimal stack on
        # callers that only need shipment-related helpers.
        from decimal import Decimal

        from djmoney.money import Money

        from shipping_acs.models import AcsCodPayout

        client = AcsClient()
        rows = client.cod_beneficiary_info(
            cod_payment_date=(
                cod_payment_date.isoformat() if cod_payment_date else ""
            ),
            user_locals=user_locals,
        )

        upserted = 0
        linked = 0
        for row in rows:
            voucher_no = row.get("Voucher_No") or row.get("voucher_no") or ""
            voucher_no = str(voucher_no).strip()
            if not voucher_no:
                continue

            payment_date = parse_datetime(row.get("COD_Payment_Date") or "")
            payment_date_only = (
                payment_date.date() if payment_date else cod_payment_date
            )

            shipment = AcsShipment.objects.filter(voucher_no=voucher_no).first()
            if shipment is not None:
                linked += 1

            defaults = {
                "shipment": shipment,
                "customer_code": str(row.get("Customer_Code", "") or "")[:32],
                "pod": str(row.get("POD", "") or "")[:255],
                "parcel_sender": str(row.get("Parcel_Sender", "") or "")[:255],
                "parcel_receiver": str(row.get("Parcel_Receiver", "") or "")[
                    :255
                ],
                "parcel_pickup_date": _parse_dt(row.get("Parcel_Pickup_Date")),
                "parcel_delivery_date": _parse_dt(
                    row.get("Parcel_Delivery_Date")
                ),
                "customer_ref_no_1": str(row.get("Customer_RefNo_1", "") or "")[
                    :32
                ],
                "customer_ref_no_2": str(row.get("Customer_RefNo_2", "") or "")[
                    :64
                ],
                "cod_amount_total": Money(
                    Decimal(str(row.get("Parcel_COD_Amount", 0) or 0)),
                    "EUR",
                ),
                "cod_amount_cash": Money(
                    Decimal(str(row.get("COD_Amount_Cach", 0) or 0)),
                    "EUR",
                ),
                "cod_amount_card": Money(
                    Decimal(str(row.get("COD_Amount_CreditCard", 0) or 0)),
                    "EUR",
                ),
                "raw_payload": row,
            }

            AcsCodPayout.objects.update_or_create(
                voucher_no=voucher_no,
                cod_payment_date=payment_date_only,
                defaults=defaults,
            )
            upserted += 1

        return {"upserted": upserted, "linked": linked, "rows": len(rows)}

    # ------------------------------------------------------------------
    # Private helpers — order status
    # ------------------------------------------------------------------

    @classmethod
    def _apply_order_status_transition(
        cls, order: Order, mapped_state: AcsShipmentState
    ) -> None:
        """Advance the Order's status based on the ACS shipment state.

        Mirrors ``BoxNowService._apply_order_status_transition`` —
        same rules, different vocabulary.
        """
        from order.exceptions import InvalidStatusTransitionError
        from order.services import OrderService

        current_status: str = order.status
        if current_status in _TERMINAL_ORDER_STATUSES:
            return

        new_status: str | None = None
        if mapped_state in _SHIPPED_STATES:
            if current_status in _PRE_SHIPPED_ORDER_STATUSES:
                new_status = "SHIPPED"
        elif mapped_state == AcsShipmentState.DELIVERED:
            new_status = "DELIVERED"
        elif mapped_state in (
            AcsShipmentState.RETURNED,
            AcsShipmentState.LOST,
        ):
            new_status = "RETURNED"
        elif mapped_state == AcsShipmentState.CANCELED:
            new_status = "CANCELED"

        if new_status is None or new_status == current_status:
            return

        try:
            OrderService.update_order_status(order, new_status)
        except InvalidStatusTransitionError as exc:
            logger.warning(
                "ACS poll: invalid order-status transition for order=%s "
                "(%r → %r): %s",
                order.id,
                current_status,
                new_status,
                exc,
            )

    @classmethod
    def _maybe_notify_arrival(
        cls,
        shipment: AcsShipment,
        new_state: AcsShipmentState,
        old_state: AcsShipmentState,
    ) -> None:
        """Trigger the arrival notification on the OUT_FOR_DELIVERY transition."""
        if (
            new_state == AcsShipmentState.OUT_FOR_DELIVERY
            and old_state != AcsShipmentState.OUT_FOR_DELIVERY
        ):
            from shipping_acs.tasks import acs_send_arrival_notification

            transaction.on_commit(
                lambda: acs_send_arrival_notification.delay(shipment.id)
            )


def _to_decimal(value: Any) -> Decimal | None:
    """Coerce an ACS lat/lng value to Decimal, or None for blanks."""
    if value in (None, "", 0):
        return None
    try:
        return Decimal(str(value))
    except (TypeError, ValueError, ArithmeticError):
        return None


def _parse_dt(value: Any) -> datetime | None:
    """Parse an ACS ISO datetime string and ensure tz-awareness.

    ACS datetimes come back as naive ISO strings (``"2020-09-05T00:00:00"``).
    We make them aware in the project's default timezone so ORM
    comparisons + admin renders behave consistently.
    """
    if not value:
        return None
    parsed = parse_datetime(str(value))
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = timezone.make_aware(parsed)
    return parsed
