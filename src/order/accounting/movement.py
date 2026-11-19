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

from core.store_settings import store_settings
from shipping.interfaces import PostalAddress
from shipping.services import ShippingService

logger = logging.getLogger(__name__)

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
class MovingGoods:
    """One good the parcel carries: the order line, its VAT rate, and how
    many of it move, the units bought less those already refunded."""

    item: int
    rate: Decimal
    quantity: int


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

    def as_json(self) -> dict[str, Any]:
        return {
            "dispatch_date": self.dispatch_date.isoformat(),
            "loading": asdict(self.loading),
            "delivery": asdict(self.delivery),
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
    AADE answered with error 228, may be registered as it was. AADE's
    documentation says nothing of error 280 (a ``dispatchDate`` before the
    current date) for a document sent with ``transmissionFailure``, so a
    receipt whose parcel left while myDATA was unreachable keeps the date
    it was issued with.

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
    if invoice.movement and not refused_for_its_content(invoice):
        issued = ReceiptMovement.from_json(invoice.movement)
        today = timezone.localdate()
        if (
            nothing_registered
            and invoice.transmission_failed_at is None
            and issued.dispatch_date < today
        ):
            return replace(issued, dispatch_date=today)
        return issued
    order = invoice.order
    loading = loading_address()
    if loading is None:
        return None
    delivery = ShippingService.delivery_address(order)
    if delivery is None:
        return None
    return ReceiptMovement(
        dispatch_date=timezone.localdate(),
        loading=loading,
        delivery=delivery,
        goods=moving_goods(order),
    )


#: Α.1123/2024 art. 7(1), as replaced by Α.1233/2026: the movement a
#: document declares starts at most this many days after it is issued.
MOVEMENT_START_MAX_DAYS = 5


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

    now = timezone.now()
    return bool(
        unanswered_sends(
            Invoice.objects.filter(
                order_id=order_id, transmission_failed_at__isnull=True
            )
        ).update(transmission_failed_at=now, updated_at=now)
    )


def flag_a_movement_the_receipt_does_not_cover(order: Any) -> None:
    """The parcel left: tell staff when its receipt, sent with movement
    indication, does not describe the movement that took place, which
    happens when the voucher was made again after the receipt was issued.

    - The parcel goes to another place than the receipt declares (a new
      locker, station or address on the new voucher).
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
    problems = []
    try:
        actual = ShippingService.delivery_address(order)
    except ValueError:
        actual = None
    if actual is not None and actual != declared.delivery:
        problems.append(
            f"it declares delivery to {_one_line(declared.delivery)}, but "
            f"the parcel goes to {_one_line(actual)}"
        )
    shipped_on = timezone.localdate(order.shipped_at or timezone.now())
    days = (shipped_on - invoice.issue_date).days
    if days > MOVEMENT_START_MAX_DAYS:
        problems.append(
            f"the parcel left {days} days after it was issued, more than "
            f"the {MOVEMENT_START_MAX_DAYS} days within which the movement "
            "it declares must start"
        )
    if not problems:
        return
    logger.error(
        "Order %s: receipt %s does not cover the movement of its parcel: %s",
        order.pk,
        invoice.invoice_number,
        "; ".join(problems),
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
    street = " ".join(part for part in (address.street, address.number) if part)
    return ", ".join(
        part
        for part in (street, f"{address.postal_code} {address.city}")
        if part
    )
