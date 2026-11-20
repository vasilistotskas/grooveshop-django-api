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
again as issued, and once its parcel leaves unregistered it is a document
whose movement started during a loss of connection, sent from then on with
AADE's indication (``transmissionFailure`` 3,
``Invoice.transmission_failed_at``, :func:`parcel_left_unregistered`).

The block is decided when the receipt is first sent and stored on the row
(``Invoice.movement``), so the registered PDF prints what AADE received,
and a resubmission sends the document as issued (``receipt_movement``).
It applies only once the store has set its dispatch point
(``movement_receipts_on``). A voucher made again after the receipt was
issued can move the parcel elsewhere, or later, than it declares; staff
are told when it leaves (:func:`flag_a_movement_the_receipt_does_not_cover`).
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from core.store_settings import store_setting, store_settings
from shipping.interfaces import PostalAddress
from shipping.services import ShippingService
from tenant.legal_identity import SELLER_SETTING_KEYS

logger = logging.getLogger(__name__)

#: The store settings naming the dispatch point (Τόπος Φόρτωσης), in the
#: order of AADE's ``AddressType``.
LOADING_ADDRESS_SETTINGS = {
    "street": "MYDATA_LOADING_STREET",
    "street_number": "MYDATA_LOADING_NUMBER",
    "postal_code": "MYDATA_LOADING_POSTAL_CODE",
    "city": "MYDATA_LOADING_CITY",
}

#: AADE's ``AddressType`` caps ``street`` and ``city`` at 150 characters.
ADDRESS_TEXT_MAX_LENGTH = 150

#: AADE's ``PartyType`` caps ``name`` at 200 characters.
PARTY_NAME_MAX_LENGTH = 200

#: The issuer is the store, a Greek business
#: (``order.accounting.mydata.service`` sends ``GR``).
ISSUER_COUNTRY = "GR"

#: Α.1123/2024 art. 7(1), as replaced by Α.1233/2026: the movement a
#: document declares starts at most this many days after it is issued.
MOVEMENT_START_MAX_DAYS = 5


def _xml_character(character: str) -> bool:
    """Whether XML 1.0 can carry *character* (its ``Char`` production): a
    payload with any other is no well-formed document."""
    point = ord(character)
    return (
        point in (0x9, 0xA, 0xD)
        or 0x20 <= point <= 0xD7FF
        or 0xE000 <= point <= 0xFFFD
        or 0x10000 <= point <= 0x10FFFF
    )


#: A Greek postcode: the dispatch point is the issuer's establishment, and
#: the issuer is Greek (``order.accounting.mydata.service`` sends ``GR``).
_GREEK_POSTCODE = re.compile(r"\d{3} ?\d{2}")


def validate_loading_text_setting(value: object) -> bool:
    """Validator for ``MYDATA_LOADING_STREET``, ``_NUMBER`` and ``_CITY``:
    text AADE accepts, or empty: within its length, and only characters an
    XML document can carry."""
    return (
        isinstance(value, str)
        and len(value) <= ADDRESS_TEXT_MAX_LENGTH
        and all(_xml_character(character) for character in value)
    )


def validate_loading_postal_code_setting(value: object) -> bool:
    """Validator for ``MYDATA_LOADING_POSTAL_CODE``: a Greek postcode, or
    empty."""
    return isinstance(value, str) and (
        value == "" or _GREEK_POSTCODE.fullmatch(value.strip()) is not None
    )


def loading_address() -> PostalAddress | None:
    """The store's dispatch point, or ``None`` while any part of it is
    blank: AADE requires the postcode and the city of every address, and
    the street number of the issuer's, which the receipt names it as
    (:func:`_issuer`; error 204, verified against the myDATA sandbox on
    2026-11-20).

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
    if not all(asdict(address).values()):
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
class MovingGoods:
    """One good the parcel carries: the order line, its VAT rate, and how
    many of it move, the units bought less those already refunded."""

    item: int
    rate: Decimal
    quantity: int


@dataclass(frozen=True)
class MovementParty:
    """The issuer or the recipient as a receipt with movement names them:
    AADE requires both with their name and address when ``isDeliveryNote``
    is true (error 204), although it refuses a Greek party's name and
    address on its other documents (errors 219, 220)."""

    name: str
    #: ISO 3166-1 alpha-2.
    country: str
    address: PostalAddress


@dataclass(frozen=True)
class ReceiptMovement:
    """What a receipt with movement indication declares about the parcel."""

    #: The day the movement starts as the receipt declares it: the day it
    #: is issued, which is the day the voucher or parcel is created
    #: (``order.tasks.issue_receipt_for_pickup``). Never a later day: a
    #: document dated after the real start of the movement misstates it.
    #: Never an earlier one: AADE refuses a ``dispatchDate`` before the
    #: current date (error 280).
    dispatch_date: date
    loading: PostalAddress
    delivery: PostalAddress
    #: Every good that physically moves, with its real quantity, decided
    #: with the rest of the block and sent as it is again: a free gift (no
    #: value, no money line) and the goods of a receipt netted by an
    #: earlier refund (one money line per rate) are declared by lines of
    #: their own with no value (myDATA REST API v2.0.2, §5.4: ``netValue``
    #: and ``vatAmount`` have a minimum of 0).
    goods: tuple[MovingGoods, ...]
    #: The store, and the buyer, as of the receipt's issue: sent as they
    #: are again on a resend.
    issuer: MovementParty
    recipient: MovementParty

    def as_json(self) -> dict[str, Any]:
        return {
            "dispatch_date": self.dispatch_date.isoformat(),
            "loading": asdict(self.loading),
            "delivery": asdict(self.delivery),
            "issuer": asdict(self.issuer),
            "recipient": asdict(self.recipient),
            "goods": [
                {
                    "item": good.item,
                    "rate": str(good.rate),
                    "quantity": good.quantity,
                }
                for good in self.goods
            ],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ReceiptMovement:
        return cls(
            dispatch_date=date.fromisoformat(data["dispatch_date"]),
            loading=PostalAddress(**data["loading"]),
            delivery=PostalAddress(**data["delivery"]),
            goods=tuple(
                MovingGoods(
                    item=good["item"],
                    rate=Decimal(good["rate"]),
                    quantity=good["quantity"],
                )
                for good in data["goods"]
            ),
            issuer=_party_from_json(data["issuer"]),
            recipient=_party_from_json(data["recipient"]),
        )


def _party_from_json(data: dict[str, Any]) -> MovementParty:
    return MovementParty(
        name=data["name"],
        country=data["country"],
        address=PostalAddress(**data["address"]),
    )


def moving_goods(order: Any) -> tuple[MovingGoods, ...]:
    """The goods *order*'s parcel carries: every line with units left once
    the refunded ones are taken off, free gifts included."""
    return tuple(
        MovingGoods(
            item=item.pk,
            rate=Decimal(item.vat_rate),
            quantity=item.quantity - item.refunded_quantity,
        )
        for item in order.items.order_by("pk")
        if item.quantity > item.refunded_quantity
    )


def refused_for_its_content(invoice: Any) -> bool:
    """Whether AADE refused *invoice* for what it says (a validation code,
    or our own check before sending: ``BUILD``, ``XSD``), so nothing of it
    was registered and it may be built anew. Error 228 is no such refusal:
    AADE holds the document under that uid already, as it was sent."""
    from order.accounting.mydata.types import ERROR_DUPLICATE_UID
    from order.models.invoice import MyDataStatus

    return (
        invoice.mydata_status == MyDataStatus.REJECTED
        and invoice.mydata_error_code != ERROR_DUPLICATE_UID
    )


def receipt_movement(
    invoice: Any, *, nothing_registered: bool = False
) -> ReceiptMovement | None:
    """The movement block of *invoice*'s receipt, or ``None`` for a plain
    receipt: the order does not move through a carrier (the merchant's own
    delivery), or the store has not set its dispatch point.

    A block already sent is the document as issued and is sent again as it
    is, its dispatch date included, unless AADE refused it for its content
    (:func:`refused_for_its_content`): a send that went unanswered, or one
    AADE answered with error 228, may be registered as it was. AADE does
    not apply error 280 (a ``dispatchDate`` before the current date) to a
    document sent with ``transmissionFailure`` (its sandbox registered one
    issued and dated the day before, and refused the same document without
    it, 2026-11-20), so a receipt whose parcel left while myDATA was
    unreachable keeps the date it was issued with.

    One date moves: AADE refuses a ``dispatchDate`` before the current
    date (error 280), so a plain receipt sent again on a later day (its
    first send went unanswered, the parcel is still with the store) is
    dated today, its addresses and goods as issued. Only when
    *nothing_registered*: AADE was asked and holds no document under its
    uid (``service.look_up_mark``), since a registered one keeps the date
    AADE has. One whose parcel left unregistered
    (``transmission_failed_at``) keeps its date, above.

    Raises ``ValueError`` when the pickup point's address is not known.
    """
    from order.models.invoice import MyDataStatus

    today = timezone.localdate()
    if invoice.mydata_status != MyDataStatus.NOT_SENT and (
        not refused_for_its_content(invoice)
    ):
        # Sent before, and it may be registered as it was: a plain receipt
        # stays plain, one with movement keeps its block.
        if not invoice.movement:
            return None
        issued = ReceiptMovement.from_json(invoice.movement)
        if (
            nothing_registered
            and invoice.transmission_failed_at is None
            and issued.dispatch_date < today
        ):
            return _starting_in_time(
                invoice, replace(issued, dispatch_date=today)
            )
        return issued
    order = invoice.order
    loading = loading_address()
    if loading is None:
        return None
    delivery = ShippingService.delivery_address(order)
    if delivery is None:
        return None
    return _starting_in_time(
        invoice,
        ReceiptMovement(
            dispatch_date=today,
            loading=_within_aade_limits(loading),
            delivery=_within_aade_limits(delivery),
            goods=moving_goods(order),
            issuer=_issuer(loading),
            recipient=_recipient(order),
        ),
    )


def _issuer(loading: PostalAddress) -> MovementParty:
    """The store as the receipt names it: its name (``INVOICE_SELLER_NAME``,
    the one the receipt prints) and, as its address, its dispatch point
    (*loading*, the establishment the parcel leaves from). AADE requires
    the issuer's street number (error 204), which the store's own address,
    one free line (``INVOICE_SELLER_ADDRESS_LINE_1``), does not hold apart.
    Raises ``ValueError``, a BUILD refusal, while the name is blank."""
    name = str(store_setting(SELLER_SETTING_KEYS["name"]) or "").strip()
    if not name:
        raise ValueError(
            "a receipt with movement names the store, and its name "
            "(INVOICE_SELLER_NAME) is blank"
        )
    return MovementParty(
        name=name[:PARTY_NAME_MAX_LENGTH],
        country=ISSUER_COUNTRY,
        address=_within_aade_limits(loading),
    )


def _recipient(order: Any) -> MovementParty:
    """The buyer as the receipt names them: their name and their own
    address, wherever the parcel goes. Raises ``ValueError``, a BUILD
    refusal, when the order lacks the name or any part of the address."""
    name = f"{order.first_name} {order.last_name}".strip()
    address = PostalAddress(
        street=(order.street or "").strip(),
        street_number=(order.street_number or "").strip(),
        postal_code=(order.zipcode or "").strip(),
        city=(order.city or "").strip(),
    )
    country = order.country.alpha_2 if order.country_id else ""
    if not (name and country and all(asdict(address).values())):
        # AADE requires the counterpart's street number too (error 204).
        raise ValueError(
            f"order #{order.pk} lacks the buyer's name or a part of their "
            "address (street, number, postcode, city, country), which a "
            "receipt with movement names"
        )
    return MovementParty(
        name=name[:PARTY_NAME_MAX_LENGTH],
        country=country,
        address=_within_aade_limits(address),
    )


def _within_aade_limits(address: PostalAddress) -> PostalAddress:
    """*address* as AADE's ``AddressType`` takes it, street and city cut
    to ``ADDRESS_TEXT_MAX_LENGTH``: decided once, here, so the payload and
    the registered PDF (which prints the stored block) say the same."""
    return replace(
        address,
        street=address.street[:ADDRESS_TEXT_MAX_LENGTH],
        city=address.city[:ADDRESS_TEXT_MAX_LENGTH],
    )


def _starting_in_time(
    invoice: Any, movement: ReceiptMovement
) -> ReceiptMovement:
    """*movement*, when its start falls within ``MOVEMENT_START_MAX_DAYS``
    of the receipt's issue and is not after the parcel left. A receipt sent
    so late that today is past that cannot declare a start AADE accepts
    (error 280 refuses an earlier date, the law a later one): it raises
    ``ValueError``, a BUILD refusal that stops it for staff (an order note,
    the "Receipts rejected by myDATA" counter) instead of sending a pair of
    dates that is wrong.

    A block built on a later day than the parcel left (a receipt AADE
    refused for its content, corrected after the pickup) would declare a
    start after the movement happened. One sent under the loss-of-
    connection procedure (``transmission_failed_at``) declares the day it
    left, a past date AADE takes on such a document; any other is the same
    BUILD refusal, for staff and the store's accountant."""
    shipped_at = invoice.order.shipped_at
    left = timezone.localdate(shipped_at) if shipped_at else None
    if left is not None and left < movement.dispatch_date:
        if invoice.transmission_failed_at is None:
            raise ValueError(
                f"the parcel of receipt {invoice.invoice_number} left on "
                f"{left}, before the receipt was registered, and a movement "
                "declared now cannot start then (AADE refuses a past "
                "start, error 280) nor later than it did; ask the store's "
                "accountant how to document it"
            )
        movement = replace(movement, dispatch_date=left)
    late = (movement.dispatch_date - invoice.issue_date).days
    if late > MOVEMENT_START_MAX_DAYS:
        raise ValueError(
            f"receipt {invoice.invoice_number} was issued on "
            f"{invoice.issue_date}, and its movement can no longer start "
            f"within the {MOVEMENT_START_MAX_DAYS} days the law allows "
            f"(it would start on {movement.dispatch_date}); ask the "
            "store's accountant how to document it"
        )
    return movement


def unanswered_sends(invoices: Any) -> Any:
    """The receipts with movement among *invoices* whose last send has no
    answer from AADE, so they may or may not be registered: one it did not
    answer, or not readably (``ERROR_NO_ANSWER``), and one whose sender
    stopped mid-request (a worker killed during the call), which is left
    SUBMITTED with no code at all; that one only once
    ``MYDATA_RETRY_AFTER_MINUTES`` passed since it was touched, as a send
    in flight right now looks the same. A receipt AADE refused, or whose
    credentials it refused, is not one: it carries AADE's code."""
    from order.accounting.mydata.types import ERROR_NO_ANSWER
    from order.models.invoice import MyDataStatus

    stalled_before = timezone.now() - timedelta(
        minutes=settings.MYDATA_RETRY_AFTER_MINUTES
    )
    return (
        invoices.filter(
            mydata_mark__isnull=True, mydata_status=MyDataStatus.SUBMITTED
        )
        .exclude(movement={})
        .filter(
            Q(mydata_error_code=ERROR_NO_ANSWER)
            | Q(mydata_error_code="", updated_at__lte=stalled_before)
        )
    )


def parcel_left_unregistered(order_id: int) -> bool:
    """The order's parcel left (SHIPPED) while its receipt with movement,
    sent, had no answer from AADE (:func:`unanswered_sends`): from now on
    the receipt is a document whose movement started during a loss of
    connection (Α.1233/2026 annex §12.1), so
    ``Invoice.transmission_failed_at`` is stamped and every later
    transmission carries ``transmissionFailure`` 3. Returns whether a
    receipt was stamped, for the caller to send it at once.

    Not before: while the parcel is still with the store, a send that went
    unanswered is sent again plainly. Not for a receipt AADE refused or
    whose credentials it refused: that is no loss of connection. A send
    that goes unanswered after the parcel left is stamped by
    ``service._persist_send_error``, so whichever comes second stamps it."""
    from order.models.invoice import Invoice

    return bool(
        stamp_the_parcels_left_unregistered(
            Invoice.objects.filter(order_id=order_id)
        )
    )


def stamp_the_parcels_left_unregistered(invoices: Any) -> int:
    """Stamp ``transmission_failed_at`` on every receipt among *invoices*
    whose parcel left (``Order.shipped_at``) while its last send has no
    answer (:func:`unanswered_sends`), before anything sends it again:
    SHIPPED does it for its order (:func:`parcel_left_unregistered`), and
    the sweep for the ones only found unanswered later (a sender that
    stopped mid-request looks unanswered only once it is stale). Returns
    how many were stamped."""
    now = timezone.now()
    return unanswered_sends(
        invoices.filter(
            order__shipped_at__isnull=False, transmission_failed_at__isnull=True
        )
    ).update(transmission_failed_at=now, updated_at=now)


def flag_a_movement_the_receipt_does_not_cover(order: Any) -> None:
    """The parcel left: tell staff when its receipt, sent with movement
    indication, does not describe the movement that took place, which
    happens when the voucher was made again after the receipt was issued.

    - The parcel goes to another place than the receipt declares (a new
      locker, station or address on the new voucher), or to a pickup point
      whose address is not known, so it cannot be compared.
    - It left more than ``MOVEMENT_START_MAX_DAYS`` after the receipt was
      issued, later than the law lets a document's movement start.

    The receipt is not touched: it is AADE's record of the sale, and how
    the movement is documented then (a new document, or a correction) is
    the accountant's call. An ERROR line raises the logged-errors alert,
    and the order note says what differs. A receipt AADE refused for its
    content is built anew when it is sent again, from the voucher as it is
    then, so it is left out, as is one canceled in myDATA.
    """
    from order.models.history import OrderHistory
    from order.models.invoice import Invoice, MyDataStatus

    invoice = (
        Invoice.objects.filter(order_id=order.pk)
        .exclude(movement={})
        .exclude(mydata_status=MyDataStatus.CANCELED)
        .first()
    )
    if invoice is None or refused_for_its_content(invoice):
        return
    declared = ReceiptMovement.from_json(invoice.movement)
    problems: list[str] = []
    #: What differs, without the customer's address: for the log.
    kinds: list[str] = []
    try:
        actual = ShippingService.delivery_address(order)
    except ValueError:
        # A pickup point whose address the catalogue lacks: whether it is
        # the one declared cannot be checked, which staff are told too.
        problems.append(
            "the parcel goes to a pickup point whose address is not known, "
            "so whether it is the declared delivery place cannot be checked"
        )
        kinds.append("delivery place (unknown)")
        actual = None
    if actual is not None and _within_aade_limits(actual) != declared.delivery:
        problems.append(
            f"it declares delivery to {_one_line(declared.delivery)}, but "
            f"the parcel goes to {_one_line(actual)}"
        )
        kinds.append("delivery place")
    if declared.goods != moving_goods(order):
        problems.append(
            "the goods it declares differ from those the parcel carries "
            "(units refunded since it was issued)"
        )
        kinds.append("goods")
    shipped_on = timezone.localdate(order.shipped_at or timezone.now())
    days = (shipped_on - invoice.issue_date).days
    if days > MOVEMENT_START_MAX_DAYS:
        problems.append(
            f"the parcel left {days} days after it was issued, more than "
            f"the {MOVEMENT_START_MAX_DAYS} days within which the movement "
            "it declares must start"
        )
        kinds.append("start")
    if not problems:
        return
    logger.error(
        "Order %s: receipt %s does not cover the movement of its parcel "
        "(%s differs); the order's notes say what",
        order.pk,
        invoice.invoice_number,
        ", ".join(kinds),
    )
    OrderHistory.log_note(
        order=order,
        note=(
            f"Receipt {invoice.invoice_number} was issued with movement "
            f"indication, but {'; and '.join(problems)}. The voucher was "
            "made again after the receipt was issued. Ask the store's "
            "accountant how to document this movement."
        ),
    )


def _one_line(address: PostalAddress) -> str:
    street = " ".join(
        part for part in (address.street, address.street_number) if part
    )
    return ", ".join(
        part
        for part in (street, f"{address.postal_code} {address.city}")
        if part
    )
