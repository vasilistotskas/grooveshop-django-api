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

The block is decided when the receipt is first sent and stored on the row
(``Invoice.movement``), so the registered PDF prints what AADE received,
and a resubmission sends the document as issued (``receipt_movement``).
It applies only once the store has set its dispatch point
(``movement_receipts_on``).
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


def loading_address() -> PostalAddress | None:
    """The store's dispatch point, or ``None`` while the postcode or the
    city, which AADE requires, is blank.

    A store without one keeps the plain receipt at SHIPPED for every order
    (``movement_receipts_on``), so setting up the platform never turns a
    courier order's receipt into a refusal; the dashboard asks for it.
    """
    values = store_settings(LOADING_ADDRESS_SETTINGS.values())
    address = PostalAddress(
        **{
            field: str(values[name] or "").strip()
            for field, name in LOADING_ADDRESS_SETTINGS.items()
        }
    )
    if not (address.postal_code and address.city):
        return None
    return address


def movement_receipts_on(order: Any) -> bool:
    """Whether *order*'s receipt carries the movement and is issued before
    the pickup: a carrier takes the parcel and the store has its dispatch
    point set."""
    return (
        ShippingService.moves_by_carrier(order)
        and loading_address() is not None
    )


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
    """The movement block of *invoice*'s receipt, or ``None`` for a plain
    receipt: the order does not move through a carrier (the merchant's own
    delivery), or the store has not set its dispatch point.

    A block already sent is the document as issued and is sent again as it
    is, its dispatch date included, unless AADE refused it: a send that
    went unanswered may have been registered. AADE's documentation says
    nothing of error 280 (a ``dispatchDate`` before the current date) for a
    document sent with ``transmissionFailure``, so a receipt issued during
    an outage keeps the date it was issued with.

    Raises ``ValueError`` when the pickup point's address is not known.
    """
    from order.models.invoice import MyDataStatus

    if invoice.movement and invoice.mydata_status != MyDataStatus.REJECTED:
        return ReceiptMovement.from_json(invoice.movement)
    order = invoice.order
    loading = loading_address()
    if loading is None:
        return None
    delivery = ShippingService.delivery_address(order)
    if delivery is None:
        return None
    planned_date, planned_time = _planned_dispatch(order)
    return ReceiptMovement(
        dispatch_date=planned_date,
        dispatch_time=planned_time,
        loading=loading,
        delivery=delivery,
    )
