"""The movement indication of an order's retail receipt.

Α.1123/2024 Annex ΙΙ §7.3 (as replaced by Α.1233/2026, ΦΕΚ Β' 6576/
30-10-2026) makes the retail receipt «με ένδειξη διακίνησης αποθεμάτων»
mandatory for goods delivered to the consumer at a place of their choice
through a postal or courier entity, unless the receipt travels on the
parcel where it can be seen or the carrier can show it at once. Neither
holds for a courier or locker parcel here, so the receipt of an order that
moves through a carrier is an 11.1 with ``isDeliveryNote``, the move
purpose «Πώληση», the dispatch date and the loading and delivery
addresses (myDATA REST API v2.0.2, §5.3 and §5.3.2), and Α.1122/2024 art.
3(1) has it transmitted «πριν την έναρξη διακίνησης»: it is issued when the
carrier's voucher or parcel is created (``order.tasks.issue_receipt_for_pickup``).
Shipping never waits for AADE: a receipt myDATA does not answer for is sent
with AADE's loss-of-connection indication (``transmissionFailure`` 3,
``Invoice.transmission_failed_at``) and transmitted again when it answers.

The block is decided when the receipt is sent and stored on the row
(``Invoice.movement``), so the registered PDF prints what AADE received. A
resubmission keeps it while its dispatch date can still be sent: AADE
refuses a ``dispatchDate`` before the current date (error 280).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date, time
from typing import Any

from django.utils import timezone

from core.store_settings import store_settings
from shipping.delivery import configured_cutoff, dispatch_date
from shipping.interfaces import PostalAddress
from shipping.services import ShippingService

#: The store settings naming the dispatch point (Τόπος Φόρτωσης), in the
#: order of AADE's ``AddressType``.
LOADING_ADDRESS_SETTINGS = {
    "street": "MYDATA_LOADING_STREET",
    "number": "MYDATA_LOADING_NUMBER",
    "postal_code": "MYDATA_LOADING_POSTAL_CODE",
    "city": "MYDATA_LOADING_CITY",
}

#: AADE's ``AddressType`` caps ``street`` and ``city`` at 150 characters.
ADDRESS_TEXT_MAX_LENGTH = 150

#: A Greek postcode: the dispatch point is the issuer's establishment, and
#: the issuer is Greek (``order.accounting.mydata.service`` sends ``GR``).
_GREEK_POSTCODE = re.compile(r"\d{3} ?\d{2}")


def validate_loading_text_setting(value: object) -> bool:
    """Validator for ``MYDATA_LOADING_STREET``, ``_NUMBER`` and ``_CITY``:
    text AADE accepts, or empty."""
    return isinstance(value, str) and len(value) <= ADDRESS_TEXT_MAX_LENGTH


def validate_loading_postal_code_setting(value: object) -> bool:
    """Validator for ``MYDATA_LOADING_POSTAL_CODE``: a Greek postcode, or
    empty."""
    return isinstance(value, str) and (
        value == "" or _GREEK_POSTCODE.fullmatch(value.strip()) is not None
    )


def loading_address() -> PostalAddress:
    """The store's dispatch point. Raises ``ValueError`` naming the
    settings when the postcode or the city, which AADE requires, is
    blank."""
    values = store_settings(LOADING_ADDRESS_SETTINGS.values())
    address = PostalAddress(
        **{
            field: str(values[name] or "").strip()
            for field, name in LOADING_ADDRESS_SETTINGS.items()
        }
    )
    if not (address.postal_code and address.city):
        raise ValueError(
            "The store's dispatch point is not set: fill in "
            "MYDATA_LOADING_POSTAL_CODE and MYDATA_LOADING_CITY (and the "
            "street) before a courier order's receipt can be sent."
        )
    return address


@dataclass(frozen=True)
class ReceiptMovement:
    """What a receipt with movement indication declares about the parcel."""

    dispatch_date: date
    #: The planned start, as the store states it (``DISPATCH_CUTOFF``);
    #: optional at issue in phase A of AADE's annex.
    dispatch_time: time | None
    loading: PostalAddress
    delivery: PostalAddress

    def as_json(self) -> dict[str, Any]:
        return {
            "dispatch_date": self.dispatch_date.isoformat(),
            "dispatch_time": self.dispatch_time.strftime("%H:%M")
            if self.dispatch_time
            else "",
            "loading": asdict(self.loading),
            "delivery": asdict(self.delivery),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ReceiptMovement:
        return cls(
            dispatch_date=date.fromisoformat(data["dispatch_date"]),
            dispatch_time=time.fromisoformat(data["dispatch_time"])
            if data["dispatch_time"]
            else None,
            loading=PostalAddress(**data["loading"]),
            delivery=PostalAddress(**data["delivery"]),
        )


def _planned_dispatch(order: Any) -> tuple[date, time | None]:
    """When the parcel is planned to leave: the order's dispatch day as the
    storefront promises it (``shipping.delivery.dispatch_date`` from now),
    at the cutoff. An order that already left (a receipt issued late) is
    dated today, the earliest AADE accepts."""
    if order.shipped_at is not None:
        return timezone.localdate(), None
    cutoff = configured_cutoff()
    return dispatch_date(timezone.now(), cutoff), cutoff


def receipt_movement(invoice: Any) -> ReceiptMovement | None:
    """The movement block of *invoice*'s receipt, or ``None`` for an order
    that does not move through a carrier (the merchant's own delivery keeps
    a plain receipt).

    Raises ``ValueError`` when an address AADE requires is unknown: the
    store's dispatch point, or the pickup point's.
    """
    order = invoice.order
    delivery = ShippingService.delivery_address(order)
    if delivery is None:
        return None
    if invoice.movement:
        stored = ReceiptMovement.from_json(invoice.movement)
        if stored.dispatch_date >= timezone.localdate():
            return stored
    planned_date, planned_time = _planned_dispatch(order)
    return ReceiptMovement(
        dispatch_date=planned_date,
        dispatch_time=planned_time,
        loading=loading_address(),
        delivery=delivery,
    )
