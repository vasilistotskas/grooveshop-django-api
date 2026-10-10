"""The demo store's shared shopper accounts, and how they are reset.

A prospect looking at a showcase can browse the catalogue as a guest,
but half of what the platform does only exists once you are signed in:
order history and tracking, saved addresses, favourites, the loyalty
ledger and tier, a gift-card balance, and — on the homepage — the
`loyalty_hero` band, which renders NOTHING for a signed-out visitor. So
the store publishes an account whose password is printed on its own
login page.

Two of them, kept apart on purpose. A B2B account changes every price
on the storefront, so a prospect handed the wholesale login would read
those numbers as the retail ones; the retail account is the one the
card advertises first.

Because the credentials are public, the account is shared and hostile
input is expected:

  - the credential mutations that could cost everyone the login are
    refused server-side (`core/demo_account.py`);
  - everything a visitor CAN change — orders, cart, reviews, comments,
    addresses, favourites, points, the gift card's balance — is wiped
    and rebuilt by `reset_demo_account`, which the nightly task runs.

What each account gets:

  - the shopper: fourteen orders across every status, with the ACS and
    BoxNow rows (and tracking events) an order in that state really has,
    invoices, a redeemed coupon, a part-spent gift card, a loyalty ledger
    that makes the account Silver, five notifications and a Cyprus
    address;
  - the buyer: an approved business profile on the wholesale group's
    price list (a VAT number that cannot belong to anyone) and a few
    invoiced orders. It signs in with the login the store already
    publishes — there is no third credential.

Orders are written with `bulk_create` and backdated, never through
`OrderService` or `save()` (see `devtools/demo_orders.py`). A seeded order
must not send a confirmation email, must not move stock, and must not
enter the state machine — it is a fixture, not a sale. The same goes for
everything that hangs off one: the carrier rows are written directly and
never reach a carrier, the invoice is issued by `generate_invoice` and
not by the task that emails and submits it to myDATA, and the
notifications skip the `post_save` that would push them to a socket.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal
from typing import Any

from core.enum import FloorChoicesEnum
from devtools.demo_shipments import ShipmentRow
from notification.enum import (
    NotificationCategoryEnum,
    NotificationKindEnum,
    NotificationTypeEnum,
)

logger = logging.getLogger(__name__)

RETAIL_EMAIL = "demo@grooveshop.space"
RETAIL_PASSWORD = "GrooveDemo-2026"
B2B_EMAIL = "demo-wholesale@grooveshop.space"
B2B_PASSWORD = "GrooveWholesale-2026"


@dataclass(frozen=True)
class AddressRow:
    title: str
    first_name: str
    last_name: str
    street: str
    street_number: str
    city: str
    zipcode: str
    floor: FloorChoicesEnum
    is_main: bool
    #: ISO 3166-1 alpha-2.
    country: str = "GR"


@dataclass(frozen=True)
class OrderRow:
    """One fixture order.

    ``items`` is ``(product_slug, quantity)``; the price is taken from
    the product at seed time so the order never disagrees with the
    catalogue a prospect is looking at.
    """

    status: str
    payment_status: str
    #: How the shopper paid (``PaySettlement``). The fixture takes the
    #: store's first pay way with that settlement, so the order's
    #: ``pay_way`` and its payment fields always agree; with no such pay
    #: way the row is skipped, like a row whose products are missing.
    settlement: str
    #: Also the fixture's identity (``metadata["demo_seed"]``), so two
    #: rows of one account must not share it.
    days_ago: int
    items: tuple[tuple[str, int], ...]
    #: The carrier row the order has, if it shipped. It supplies the
    #: order's tracking number and carrier too.
    shipment: ShipmentRow | None = None
    #: The address it went to is the account's own in this country.
    country: str = "GR"
    #: EUR, as a string so the dataset never carries a float.
    shipping_price: str = "3.50"
    customer_notes: str = ""
    #: A coupon code from ``devtools/demo_promotions.py``; the order
    #: carries its discount and its redemption row.
    promotion_code: str = ""
    #: Spends ``GIFT_CARD_SPENT`` of the demo gift card.
    gift_card: bool = False
    #: Issues an invoice (and renders its PDF).
    invoice: bool = False
    #: Every line went back.
    refunded: bool = False


ADDRESSES: tuple[AddressRow, ...] = (
    AddressRow(
        title="Σπίτι",
        first_name="Δήμος",
        last_name="Δοκιμής",
        street="Τσιμισκή",
        street_number="100",
        city="Θεσσαλονίκη",
        zipcode="54622",
        floor=FloorChoicesEnum.THIRD_FLOOR,
        is_main=True,
    ),
    AddressRow(
        title="Γραφείο",
        first_name="Δήμος",
        last_name="Δοκιμής",
        street="Πανεπιστημίου",
        street_number="42",
        city="Αθήνα",
        zipcode="10679",
        floor=FloorChoicesEnum.FIRST_FLOOR,
        is_main=False,
    ),
    AddressRow(
        title="Κύπρος",
        first_name="Δήμος",
        last_name="Δοκιμής",
        street="Λήδρας",
        street_number="52",
        city="Λευκωσία",
        zipcode="1011",
        floor=FloorChoicesEnum.SECOND_FLOOR,
        is_main=False,
        country="CY",
    ),
)

#: The wholesale account is a business, not a traveller: it keeps the
#: two Greek addresses it always had.
WHOLESALE_ADDRESSES: tuple[AddressRow, ...] = ADDRESSES[:2]

#: Favourites are product slugs from ``devtools/demo_catalogue.py``. A
#: slug that no longer exists is skipped rather than failing the seed —
#: the catalogue is edited far more often than this list.
FAVOURITE_SLUGS: tuple[str, ...] = (
    "demo-powerbank-20k",
    "demo-cable-usbc-braided-black",
    "demo-earbuds-black",
    "demo-charger-gan-65w",
    "demo-case-clear",
    "demo-glass-privacy",
)

#: Where the parcels go. The lockers are named, never written to
#: ``BoxNowLocker`` (see ``devtools/demo_shipments.py``).
_ACS_STATION = "ACS Θεσσαλονίκη — Κέντρο"
_ACS_STATION_ATHENS = "ACS Αθήνα — Κέντρο"
_LOCKER_THESSALONIKI = ShipmentRow(
    "boxnow", "", "", "BOX NOW Locker · Τσιμισκή 70", "demo-apm-3001"
)
_LOCKER_CYPRUS = ShipmentRow(
    "boxnow",
    "",
    "",
    "BOX NOW Locker · Λευκωσία, Λήδρας",
    "demo-apm-6001",
    destination_country="CY",
)


def _shipment(base: ShipmentRow, state: str, voucher: str) -> ShipmentRow:
    """A row for ``voucher`` in ``state``, going where ``base`` goes."""
    return replace(base, state=state, voucher=voucher)


#: Each row is a state a real order can hold. A paid order that was
#: delivered is COMPLETED — ``complete_paid_delivered_orders`` moves any
#: DELIVERED + paid order there within the hour, so a DELIVERED fixture
#: only ever re-entered the state machine. The collect-on-delivery
#: parcel to a BoxNow locker settles at the locker terminal.
#:
#: Fourteen orders, newest first: two on their way with a courier, one
#: in a BoxNow depot, a spread of delivered ones across ACS and BoxNow
#: (one to Cyprus), a return, a refund, a cancellation, and orders that
#: carry a coupon, a gift-card spend and an invoice. The parcels still in
#: flight are the four newest, so their tracking is always fresh.
ORDERS: tuple[OrderRow, ...] = (
    OrderRow(
        status="PROCESSING",
        payment_status="COMPLETED",
        settlement="online",
        days_ago=1,
        items=(("demo-charger-gan-65w", 1), ("demo-case-clear", 1)),
    ),
    OrderRow(
        status="PROCESSING",
        payment_status="PENDING",
        settlement="courier_cash",
        days_ago=2,
        items=(
            ("demo-wireless-pad-white", 1),
            ("demo-cable-usbc-1m-white", 2),
        ),
        shipment=ShipmentRow(
            "acs", "new", "DEMO0000000002", _ACS_STATION, "", "GR"
        ),
    ),
    OrderRow(
        status="SHIPPED",
        payment_status="PENDING",
        settlement="courier_cash",
        days_ago=4,
        items=(
            ("demo-speaker-mint", 1),
            ("demo-cable-usbc-braided-black", 1),
        ),
        shipment=ShipmentRow(
            "acs", "out_for_delivery", "DEMO0000000004", _ACS_STATION
        ),
        shipping_price="4.20",
    ),
    OrderRow(
        status="SHIPPED",
        payment_status="PENDING",
        settlement="carrier_terminal",
        days_ago=5,
        items=(("demo-earbuds-black", 1),),
        shipment=_shipment(_LOCKER_THESSALONIKI, "in_depot", "DEMO0000000005"),
        shipping_price="2.50",
        customer_notes="Παράδοση μετά τις 17:00 παρακαλώ.",
    ),
    OrderRow(
        status="COMPLETED",
        payment_status="COMPLETED",
        settlement="online",
        days_ago=11,
        items=(("demo-powerbank-10k-white", 1), ("demo-charger-20w-white", 1)),
        shipment=_shipment(_LOCKER_THESSALONIKI, "delivered", "DEMO0000000011"),
        shipping_price="2.50",
        promotion_code="SAVE5",
        invoice=True,
    ),
    OrderRow(
        status="COMPLETED",
        payment_status="COMPLETED",
        settlement="courier_cash",
        days_ago=15,
        items=(("demo-mount-car-vent", 1), ("demo-charger-car-30w", 1)),
        shipment=ShipmentRow(
            "acs", "delivered", "DEMO0000000015", _ACS_STATION
        ),
        shipping_price="4.20",
        invoice=True,
    ),
    OrderRow(
        status="COMPLETED",
        payment_status="COMPLETED",
        settlement="online",
        days_ago=21,
        items=(
            ("demo-powerbank-20k", 1),
            ("demo-cable-usbc-braided-black", 2),
        ),
        shipment=ShipmentRow(
            "acs", "delivered", "DEMO0000000021", _ACS_STATION_ATHENS
        ),
        invoice=True,
    ),
    OrderRow(
        status="RETURNED",
        payment_status="COMPLETED",
        settlement="online",
        days_ago=26,
        items=(("demo-speaker-mini-grey", 1),),
        shipment=_shipment(_LOCKER_THESSALONIKI, "returned", "DEMO0000000026"),
        shipping_price="2.50",
    ),
    OrderRow(
        status="COMPLETED",
        payment_status="COMPLETED",
        settlement="carrier_terminal",
        days_ago=33,
        items=(
            ("demo-powerbank-magnetic", 1),
            ("demo-case-clear-magnetic", 1),
        ),
        shipment=_shipment(_LOCKER_CYPRUS, "delivered", "DEMO0000000033"),
        country="CY",
        shipping_price="5.90",
    ),
    OrderRow(
        status="CANCELED",
        payment_status="CANCELED",
        settlement="online",
        days_ago=40,
        items=(("demo-glass-privacy", 1),),
    ),
    OrderRow(
        status="REFUNDED",
        payment_status="REFUNDED",
        settlement="online",
        days_ago=47,
        items=(("demo-wireless-duo", 1),),
        shipment=ShipmentRow(
            "acs", "returned", "DEMO0000000047", _ACS_STATION_ATHENS
        ),
        refunded=True,
    ),
    OrderRow(
        status="COMPLETED",
        payment_status="COMPLETED",
        settlement="online",
        days_ago=55,
        items=(
            ("demo-stand-desk-black", 1),
            ("demo-organiser-cable", 1),
            ("demo-glass-2pack", 1),
        ),
        shipment=_shipment(_LOCKER_THESSALONIKI, "delivered", "DEMO0000000055"),
        shipping_price="2.50",
        gift_card=True,
    ),
    OrderRow(
        status="COMPLETED",
        payment_status="COMPLETED",
        settlement="courier_cash",
        days_ago=63,
        items=(("demo-charger-gan-45w", 1), ("demo-cable-usbc-2m-black", 1)),
        shipment=ShipmentRow(
            "acs", "delivered", "DEMO0000000063", _ACS_STATION
        ),
        shipping_price="4.20",
        invoice=True,
    ),
    OrderRow(
        status="COMPLETED",
        payment_status="COMPLETED",
        settlement="online",
        days_ago=72,
        items=(("demo-case-rugged", 1), ("demo-glass-camera", 1)),
    ),
)

#: How long after it was placed an order last changed state, by status.
STATUS_AGE_DAYS: dict[str, int] = {
    "PROCESSING": 0,
    "SHIPPED": 1,
    "CANCELED": 1,
    "COMPLETED": 6,
    "RETURNED": 9,
    "REFUNDED": 12,
}


@dataclass(frozen=True)
class PointsRow:
    transaction_type: str
    points: int
    description: str
    #: The ``days_ago`` of the order the points belong to, if any.
    order: int | None
    days_ago: int


#: A ledger a prospect can read: a welcome bonus, earnings from every
#: completed order, and two redemptions. The XP the earnings add up to
#: (``4250``) is what lifts the account to level 5 — Silver — and the
#: balance is what is left after the redemptions.
POINTS: tuple[PointsRow, ...] = (
    PointsRow("BONUS", 250, "Καλωσόρισμα", None, 80),
    PointsRow("EARN", 450, "Πόντοι από παραγγελία", 72, 72),
    PointsRow("EARN", 540, "Πόντοι από παραγγελία", 63, 63),
    PointsRow("EARN", 610, "Πόντοι από παραγγελία", 55, 55),
    PointsRow("REDEEM", -200, "Εξαργύρωση 200 πόντων σε έκπτωση", None, 48),
    PointsRow("EARN", 560, "Πόντοι από παραγγελία", 33, 33),
    PointsRow("EARN", 720, "Πόντοι από παραγγελία", 21, 21),
    PointsRow("EARN", 480, "Πόντοι από παραγγελία", 15, 15),
    PointsRow("EARN", 640, "Πόντοι από παραγγελία", 11, 11),
    PointsRow("REDEEM", -350, "Εξαργύρωση 350 πόντων σε έκπτωση", None, 8),
)

GIFT_CARD_CODE = "DEMO-GIFT-25"
GIFT_CARD_AMOUNT = Decimal("25.00")
#: What the card has already paid towards an order, so a prospect meets
#: a card with a history rather than a box-fresh one. Seeded once and
#: kept: the nightly reset relinks it to the rebuilt order instead of
#: writing a fresh spend every night onto an append-only ledger.
GIFT_CARD_SPENT = Decimal("8.50")
GIFT_CARD_BALANCE = GIFT_CARD_AMOUNT - GIFT_CARD_SPENT
GIFT_CARD_SPEND_NOTE = "Demo store showcase: part-spent card"

#: The coupon the account redeemed has to be a fixed-amount one — the
#: only kind the fixture knows how to price.
REDEEMED_BENEFIT = "FIXED_AMOUNT"


@dataclass(frozen=True)
class NotificationRow:
    notification_type: str
    kind: str
    category: str
    #: The ``days_ago`` of the order it is about; sets its link.
    order: int | None
    link: str
    title_el: str
    title_en: str
    message_el: str
    message_en: str
    days_ago: int
    seen: bool


#: Five, a mix of read and unread, covering the places a notification
#: links to: an order, the loyalty page and the offers.
NOTIFICATIONS: tuple[NotificationRow, ...] = (
    NotificationRow(
        NotificationTypeEnum.ACS_OUT_FOR_DELIVERY,
        NotificationKindEnum.INFO,
        NotificationCategoryEnum.SHIPPING,
        4,
        "",
        "Η παραγγελία σου είναι σε διανομή",
        "Your order is out for delivery",
        "Ο κούριερ της ACS έχει το δέμα σου και θα φτάσει σήμερα.",
        "The ACS courier has your parcel and will arrive today.",
        0,
        False,
    ),
    NotificationRow(
        NotificationTypeEnum.ORDER_SHIPPED,
        NotificationKindEnum.INFO,
        NotificationCategoryEnum.ORDER,
        5,
        "",
        "Η παραγγελία σου έφυγε από την αποθήκη",
        "Your order has left the warehouse",
        "Το δέμα σου είναι καθ' οδόν προς το BOX NOW locker που διάλεξες.",
        "Your parcel is on its way to the BOX NOW locker you chose.",
        1,
        False,
    ),
    NotificationRow(
        NotificationTypeEnum.LOYALTY_TIER_UP,
        NotificationKindEnum.SUCCESS,
        NotificationCategoryEnum.ACCOUNT,
        None,
        "/account/loyalty",
        "Έγινες Ασημένιο μέλος",
        "You are now a Silver member",
        "Κερδίζεις πια 25% περισσότερους πόντους σε κάθε παραγγελία.",
        "You now earn 25% more points on every order.",
        3,
        False,
    ),
    NotificationRow(
        NotificationTypeEnum.PRICE_DROP_FAVOURITE,
        NotificationKindEnum.INFO,
        NotificationCategoryEnum.PRODUCT,
        None,
        "/offers",
        "Έπεσε η τιμή ενός αγαπημένου σου",
        "A favourite of yours dropped in price",
        "Το Powerbank 20.000mAh έχει τώρα έκπτωση — δες όλες τις προσφορές.",
        "The 20,000mAh power bank is on offer now: see every offer.",
        6,
        True,
    ),
    NotificationRow(
        NotificationTypeEnum.ORDER_DELIVERED,
        NotificationKindEnum.SUCCESS,
        NotificationCategoryEnum.ORDER,
        11,
        "",
        "Η παραγγελία σου παραδόθηκε",
        "Your order was delivered",
        "Το δέμα σου παραλήφθηκε από το locker. Ευχαριστούμε!",
        "Your parcel was collected from the locker. Thank you!",
        9,
        True,
    ),
)


# ── the wholesale account ────────────────────────────────────────────

#: A business that cannot exist. ``999999993`` passes the ΑΦΜ checksum
#: (``b2b.validators.is_valid_greek_vat``) so a wholesale checkout
#: accepts it, and reads as a placeholder. ``EL`` + nine digits is the
#: VIES form; the profile stores the nine digits, as the platform
#: normalises them. It was never submitted to VIES, so its status stays
#: ``UNCHECKED`` rather than claiming a verification that did not happen.
WHOLESALE_COMPANY = "Δοκιμαστική Χονδρική ΙΚΕ"
WHOLESALE_VAT_ID = "999999993"
WHOLESALE_VAT_COUNTRY = "GR"
WHOLESALE_TAX_OFFICE = "ΔΟΥ Θεσσαλονίκης"
WHOLESALE_ACTIVITY = "Χονδρικό εμπόριο αξεσουάρ κινητής τηλεφωνίας"
WHOLESALE_BILLING = ("Λεωφόρος Νίκης", "12", "Θεσσαλονίκη", "54621")

#: A few orders on the group's price list, paid the ways the store
#: really offers (a card online, cash to the courier); the invoice is the
#: document a business needs.
WHOLESALE_ORDERS: tuple[OrderRow, ...] = (
    OrderRow(
        status="PROCESSING",
        payment_status="COMPLETED",
        settlement="online",
        days_ago=2,
        items=(
            ("demo-powerbank-10k-black", 12),
            ("demo-charger-gan-65w", 6),
            ("demo-earbuds-white", 6),
        ),
        shipping_price="0.00",
    ),
    OrderRow(
        status="COMPLETED",
        payment_status="COMPLETED",
        settlement="online",
        days_ago=9,
        items=(
            ("demo-cable-usbc-1m-black", 24),
            ("demo-charger-gan-45w", 10),
            ("demo-glass-2pack", 12),
            ("demo-case-clear", 12),
        ),
        shipment=ShipmentRow(
            "acs", "delivered", "DEMO0000001009", _ACS_STATION
        ),
        shipping_price="0.00",
        invoice=True,
    ),
    OrderRow(
        status="COMPLETED",
        payment_status="COMPLETED",
        settlement="courier_cash",
        days_ago=24,
        items=(
            ("demo-cable-usbc-braided-black", 20),
            ("demo-speaker-mini-grey", 8),
            ("demo-charger-gan-45w", 6),
        ),
        shipment=ShipmentRow(
            "acs", "delivered", "DEMO0000001024", _ACS_STATION_ATHENS
        ),
        shipping_price="0.00",
        invoice=True,
    ),
)


#: Everything a visitor can create while signed in as a shared account.
#: The nightly reset removes all of it before rebuilding the fixtures.
_VISITOR_OWNED = (
    "orders",
    "reviews",
    "blog comments",
    "carts",
    "points",
    "notifications",
    "sessions and tokens",
)


def _bump(report: dict[str, int], key: str, amount: int = 1) -> None:
    report[key] = report.get(key, 0) + amount


def _ensure_user(email: str, password: str, first: str, last: str):
    """Get-or-create the account with a VERIFIED primary address.

    ``ACCOUNT_EMAIL_VERIFICATION`` is mandatory and
    ``ACCOUNT_LOGIN_METHODS`` is ``{"email"}``, so an account whose
    EmailAddress row is unverified cannot sign in at all — the card
    would publish credentials that do not work.

    ``set_password`` here is the MODEL method, which is deliberately not
    the adapter's: the adapter refuses a change on this very account.
    """
    from allauth.account.models import EmailAddress
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    user, created = user_model.objects.get_or_create(
        email=email,
        defaults={
            "first_name": first,
            "last_name": last,
            "is_active": True,
        },
    )
    user.first_name = first
    user.last_name = last
    user.is_active = True
    user.set_password(password)
    user.save()

    EmailAddress.objects.update_or_create(
        user=user,
        email=email,
        defaults={"verified": True, "primary": True},
    )
    return user, created


def _seed_addresses(user, rows: Sequence[AddressRow] = ADDRESSES) -> int:
    from country.models import Country
    from user.models.address import UserAddress

    countries = {
        country.alpha_2: country
        for country in Country.objects.filter(
            alpha_2__in={row.country for row in rows}
        )
    }
    written = 0
    for row in rows:
        _address, created = UserAddress.objects.update_or_create(
            user=user,
            title=row.title,
            defaults={
                "first_name": row.first_name,
                "last_name": row.last_name,
                "street": row.street,
                "street_number": row.street_number,
                "city": row.city,
                "zipcode": row.zipcode,
                "floor": row.floor,
                "is_main": row.is_main,
                "country": countries.get(row.country),
            },
        )
        written += int(created)
    return written


def _seed_favourites(user) -> int:
    from product.models import Product
    from product.models.favourite import ProductFavourite

    written = 0
    for slug in FAVOURITE_SLUGS:
        product = Product.objects.filter(slug=slug).first()
        if product is None:
            continue
        _row, created = ProductFavourite.objects.get_or_create(
            user=user, product=product
        )
        written += int(created)
    return written


def _address_for(row: OrderRow, addresses: Sequence[AddressRow]) -> AddressRow:
    """The account's own address in the order's country, main one first."""
    candidates = [a for a in addresses if a.country == row.country]
    return next((a for a in candidates if a.is_main), candidates[0])


def _coupon(row: OrderRow, subtotal: Decimal):
    """The code, and the discount it gives, for an order that redeems one.

    ``None`` when the store has no such code (a store that never ran the
    promotions step): the order is then written without a discount rather
    than with one nothing backs. A code the fixture cannot price raises —
    that is a mistake in the dataset, not in the store.
    """
    from djmoney.money import Money

    from promotion.models.code import PromotionCode

    if not row.promotion_code:
        return None
    code = (
        PromotionCode.objects.select_related("promotion")
        .filter(code=row.promotion_code)
        .first()
    )
    if code is None:
        return None
    promotion = code.promotion
    if promotion.benefit_type != REDEEMED_BENEFIT:
        raise ValueError(
            f"{row.promotion_code}: only {REDEEMED_BENEFIT} coupons are priced"
        )
    minimum = promotion.min_subtotal
    if minimum is not None and subtotal < minimum.amount:
        raise ValueError(
            f"{row.promotion_code} needs {minimum}; the order is {subtotal}"
        )
    return code, Money(promotion.benefit_value, "EUR")


def _seed_orders(
    user,
    rows: Sequence[OrderRow],
    *,
    addresses: Sequence[AddressRow],
    profile=None,
) -> tuple[int, list[tuple[OrderRow, Any]]]:
    """Fixture orders, written silently and backdated.

    Returns how many were written and every ``(row, order)`` the account
    now has, so the follow-ups (carrier rows, redemptions, invoices) can
    run on orders that already existed too — a re-run converges.

    The writing itself is ``devtools.demo_orders.create_orders``; read
    its docstring for why none of this goes through ``save()``. With
    ``profile`` the account is a business: the lines are priced from its
    group's price list, as a wholesale cart does, and the order carries
    the buyer's company details and an invoice document type.
    """
    from django.utils import timezone
    from djmoney.money import Money

    from country.models import Country
    from devtools.demo_orders import FixtureLine, FixtureOrder, create_orders
    from giftcard.models import GiftCard
    from order.models.order import Order
    from pay_way.models import PayWay
    from product.models import Product
    from shipping.enum import ShippingKind
    from shipping.models.provider import ShippingProvider

    now = timezone.now()
    existing = {
        order.metadata["demo_seed"]: order
        for order in Order.objects.filter(
            user=user, metadata__has_key="demo_seed"
        )
    }
    countries = {
        country.alpha_2: country
        for country in Country.objects.filter(
            alpha_2__in={row.country for row in rows}
        )
    }
    providers = {p.code: p for p in ShippingProvider.objects.all()}
    products = {
        product.slug: product
        for product in Product.objects.filter(
            slug__in={slug for row in rows for slug, _qty in row.items}
        ).select_related("vat")
    }
    card_exists = GiftCard.objects.filter(code=GIFT_CARD_CODE).exists()

    if profile is not None:
        from b2b.services import B2BPricingService

        def unit_price(product):
            return B2BPricingService.resolve_single(
                product, profile.customer_group
            ).final
    else:

        def unit_price(product):
            return product.final_price

    fixtures: list[FixtureOrder] = []
    pending: list[OrderRow] = []
    for row in rows:
        if row.days_ago in existing:
            continue
        lines = [
            FixtureLine(
                products[slug],
                quantity,
                unit_price(products[slug]),
                row.refunded,
            )
            for slug, quantity in row.items
            if slug in products
        ]
        pay_way = (
            PayWay.objects.filter(settlement=row.settlement)
            .order_by("id")
            .first()
        )
        if not lines or pay_way is None:
            continue

        address = _address_for(row, addresses)
        carrier = row.shipment.carrier if row.shipment else None
        subtotal = sum(
            (line.price.amount * line.quantity for line in lines), Decimal(0)
        )
        coupon = _coupon(row, subtotal)
        paid = row.payment_status in ("COMPLETED", "REFUNDED")
        placed_at = now - timedelta(days=row.days_ago)
        metadata: dict[str, Any] = {"demo_seed": row.days_ago}
        fields: dict[str, Any] = {
            "user": user,
            "email": user.email,
            "first_name": address.first_name,
            "last_name": address.last_name,
            "street": address.street,
            "street_number": address.street_number,
            "city": address.city,
            "zipcode": address.zipcode,
            "floor": address.floor,
            "country": countries.get(row.country),
            "pay_way": pay_way,
            "status": row.status,
            "payment_status": row.payment_status,
            # The gateway that took the money (docs/order-system.md §1),
            # so only an order whose money moved names one.
            "payment_method": pay_way.provider_code if paid else "",
            "payment_method_fee": pay_way.cost,
            "shipping_price": Money(Decimal(row.shipping_price), "EUR"),
            "shipping_provider": providers.get(carrier or "flat_rate"),
            "shipping_kind": (
                ShippingKind.PICKUP_POINT
                if carrier == "boxnow"
                else ShippingKind.HOME_DELIVERY
            ),
            "tracking_number": row.shipment.voucher if row.shipment else "",
            "shipping_carrier": carrier or "",
            "customer_notes": row.customer_notes,
        }
        if coupon is not None:
            code, discount = coupon
            fields["discount_amount"] = discount
            metadata["promotions"] = [
                {
                    "promotion_id": code.promotion_id,
                    "name": code.promotion.safe_translation_getter(
                        "name", any_language=True
                    )
                    or "",
                    "code": code.code,
                    "amount": str(discount.amount),
                }
            ]
        if row.gift_card and card_exists:
            fields["gift_card_amount"] = Money(GIFT_CARD_SPENT, "EUR")
            metadata["gift_cards"] = [
                {"code": GIFT_CARD_CODE, "amount": str(GIFT_CARD_SPENT)}
            ]
        if profile is not None:
            street, number, city, zipcode = WHOLESALE_BILLING
            group = profile.customer_group
            fields.update(
                document_type="INVOICE",
                billing_vat_id=profile.vat_id,
                billing_country=WHOLESALE_VAT_COUNTRY,
                billing_company_name=profile.company_name,
                billing_tax_office=profile.tax_office,
                billing_activity=profile.activity,
                billing_street=street,
                billing_street_number=number,
                billing_city=city,
                billing_zipcode=zipcode,
            )
            metadata["b2b_pricing"] = {
                "group_id": group.pk,
                "group_name": group.name,
                "discount_percent": str(group.discount_percent),
            }
        fixtures.append(
            FixtureOrder(
                fields=fields,
                lines=lines,
                placed_at=placed_at,
                updated_at=min(
                    now,
                    placed_at + timedelta(days=STATUS_AGE_DAYS[row.status]),
                ),
                metadata=metadata,
            )
        )
        pending.append(row)

    for row, order in zip(pending, create_orders(fixtures), strict=True):
        existing[row.days_ago] = order
    return len(pending), [
        (row, existing[row.days_ago])
        for row in rows
        if row.days_ago in existing
    ]


def _seed_shipment(row: OrderRow, order, now) -> bool:
    """The ACS voucher or BoxNow parcel the order shipped under."""
    from djmoney.money import Money

    from devtools.demo_shipments import (
        create_acs_shipment,
        create_boxnow_shipment,
    )
    from pay_way.enum.settlement import PaySettlement
    from shipping_acs.models import AcsShipment
    from shipping_boxnow.models import BoxNowShipment

    shipment = row.shipment
    if shipment is None:
        return False
    model = AcsShipment if shipment.carrier == "acs" else BoxNowShipment
    if model.objects.filter(order=order).exists():
        return False

    weight = sum(
        int(item.product.weight.g) * item.quantity
        for item in order.items.select_related("product")
        if item.product.weight
    )
    collected = (
        PaySettlement(order.pay_way.settlement)
        in PaySettlement.collected_on_delivery()
    )
    owed = order.paid_amount if collected else Money(0, "EUR")
    if shipment.carrier == "acs":
        create_acs_shipment(
            order,
            shipment,
            placed_at=order.created_at,
            now=now,
            cod_amount=owed,
            weight_grams=weight,
            pickup_point=False,
        )
    else:
        create_boxnow_shipment(
            order,
            shipment,
            placed_at=order.created_at,
            now=now,
            amount_to_collect=owed,
            weight_grams=weight,
        )
    return True


def _seed_redemption(order, user) -> bool:
    """The coupon the order redeemed, as the usage-limit ledger sees it."""
    from djmoney.money import Money

    from promotion.models.code import PromotionCode
    from promotion.models.redemption import PromotionRedemption

    written = False
    for entry in order.metadata.get("promotions", []):
        code = (
            PromotionCode.objects.select_related("promotion")
            .filter(code=entry["code"])
            .first()
        )
        if code is None:
            continue
        redemption, created = PromotionRedemption.objects.get_or_create(
            promotion=code.promotion,
            order=order,
            defaults={
                "code": code,
                "user": user,
                "email": order.email,
                "amount": Money(Decimal(entry["amount"]), "EUR"),
            },
        )
        if created:
            PromotionRedemption.objects.filter(pk=redemption.pk).update(
                created_at=order.created_at, updated_at=order.created_at
            )
            written = True
    return written


def _seed_gift_spend(order) -> bool:
    """The part of the demo gift card this order paid for.

    One ledger row for the card's whole life. The reset deletes the
    order, which sets the row's ``order`` to NULL; the next seed relinks
    that row to the rebuilt order. Writing a fresh spend each night
    instead would put a REDEEM and a compensating ADJUST per night onto a
    ledger that is append-only by design.
    """
    from giftcard.enum import GiftCardTransactionKind
    from giftcard.models import GiftCard, GiftCardTransaction

    card = GiftCard.objects.filter(code=GIFT_CARD_CODE).first()
    if card is None or not order.gift_card_amount:
        return False
    spend = card.transactions.filter(
        kind=GiftCardTransactionKind.REDEEM, description=GIFT_CARD_SPEND_NOTE
    ).first()
    if spend is None:
        spend = GiftCardTransaction.objects.create(
            gift_card=card,
            kind=GiftCardTransactionKind.REDEEM,
            amount=-GIFT_CARD_SPENT,
            order=order,
            description=GIFT_CARD_SPEND_NOTE,
        )
        GiftCardTransaction.objects.filter(pk=spend.pk).update(
            created_at=order.created_at, updated_at=order.created_at
        )
        return True
    if spend.order_id == order.pk:
        return False
    GiftCardTransaction.objects.filter(pk=spend.pk).update(order=order)
    return True


def _seed_invoice(order) -> bool:
    """Issue the order's invoice, dated the day after it was placed.

    Through ``generate_invoice`` — the numbering, snapshots and PDF are
    the real ones — and not through ``generate_order_invoice``, the task
    that also emails the buyer and submits the document to myDATA. The
    first call dates the invoice today, so its date is corrected and the
    document rendered again: a PDF that says today beside an order from
    three weeks ago would be the first thing a prospect notices.
    """
    from django.utils import timezone

    from order.invoicing import generate_invoice
    from order.models.invoice import Invoice

    if Invoice.objects.filter(order=order).exists():
        return False
    invoice = generate_invoice(order)
    issued = min(
        (order.created_at + timedelta(days=1)).date(), timezone.localdate()
    )
    Invoice.objects.filter(pk=invoice.pk).update(
        issue_date=issued,
        created_at=order.created_at,
        updated_at=order.created_at,
    )
    generate_invoice(order, force=True)
    return True


def _seed_points(user, orders: dict[int, Any]) -> int:
    """The loyalty ledger, and the XP and tier it adds up to.

    The tier is whatever the account's XP earns under the store's own
    ladder (``LoyaltyService.get_user_tier``), never a name written here.
    It is written with ``update()``: saving the user would fire
    ``loyalty_tier_changed``, which pushes a live "you are now Silver"
    notification the prospect never earned in this session.
    """
    from django.utils import timezone

    from loyalty.models.transaction import PointsTransaction
    from loyalty.services import LoyaltyService

    now = timezone.now()
    written = 0
    for row in POINTS:
        order = orders.get(row.order) if row.order is not None else None
        description = (
            f"{row.description} #{order.pk}" if order else row.description
        )
        transaction, created = PointsTransaction.objects.get_or_create(
            user=user,
            transaction_type=row.transaction_type,
            description=description,
            defaults={"points": row.points, "reference_order": order},
        )
        if created:
            moment = now - timedelta(days=row.days_ago)
            PointsTransaction.objects.filter(pk=transaction.pk).update(
                created_at=moment, updated_at=moment
            )
            written += 1

    # XP only ever goes up: the redemptions spend points, not experience.
    user.total_xp = sum(row.points for row in POINTS if row.points > 0)
    tier = LoyaltyService.get_user_tier(user)
    type(user).objects.filter(pk=user.pk).update(
        total_xp=user.total_xp, loyalty_tier=tier
    )
    user.loyalty_tier = tier
    return written


def _seed_notifications(user, orders: dict[int, Any]) -> int:
    """Five in-app notifications, some read, linking real places.

    The ``Notification`` is saved (nothing listens to it) but its
    ``NotificationUser`` is ``bulk_create``d: a ``post_save`` on that
    row queues the live WebSocket push, and a fixture must not announce
    itself to whoever is signed in.
    """
    from django.utils import timezone

    from notification.models import Notification, NotificationUser

    now = timezone.now()
    written = 0
    for row in NOTIFICATIONS:
        order = orders.get(row.order) if row.order is not None else None
        link = f"/account/orders/{order.pk}" if order else row.link
        if NotificationUser.objects.filter(
            user=user,
            notification__notification_type=row.notification_type,
            notification__link=link,
        ).exists():
            continue
        notification = Notification(
            kind=row.kind,
            category=row.category,
            notification_type=row.notification_type,
            link=link,
        )
        notification.set_current_language("el")
        notification.title = row.title_el
        notification.message = row.message_el
        notification.set_current_language("en")
        notification.title = row.title_en
        notification.message = row.message_en
        notification.save()
        moment = now - timedelta(days=row.days_ago)
        Notification.objects.filter(pk=notification.pk).update(
            created_at=moment, updated_at=moment
        )
        [link_row] = NotificationUser.objects.bulk_create(
            [
                NotificationUser(
                    user=user,
                    notification=notification,
                    seen=row.seen,
                    seen_at=moment if row.seen else None,
                )
            ]
        )
        NotificationUser.objects.filter(pk=link_row.pk).update(
            created_at=moment, updated_at=moment
        )
        written += 1
    return written


def _seed_gift_card(user) -> bool:
    """Issue the demo gift card through the service, on a fixed code.

    A PURCHASE needs a live payment, so the balance a prospect checks is
    an ADMIN-issued card rather than a bought one.

    The service generates a crypto-random code — correct for a real
    card, useless for a demo, because the card only demonstrates
    anything if the code is printed somewhere a visitor can copy. So it
    is renamed afterwards, which keeps the issue ledger the service
    writes rather than hand-rolling a card around it.
    """
    from djmoney.money import Money

    from giftcard.models import GiftCard
    from giftcard.services import GiftCardService

    if GiftCard.objects.filter(code=GIFT_CARD_CODE).exists():
        return False
    try:
        card = GiftCardService.issue(
            Money(GIFT_CARD_AMOUNT, "EUR"),
            issued_to=user,
            recipient_email=user.email,
            description="Demo store showcase card",
        )
        GiftCard.objects.filter(pk=card.pk).update(code=GIFT_CARD_CODE)
    except Exception:
        logger.warning("Could not issue the demo gift card", exc_info=True)
        return False
    return True


def _seed_business_profile(user):
    """The wholesale account's approved business profile.

    Approved straight away, written directly: ``B2BService.approve``
    emails the applicant, and the profile is a fixture. In the group the
    wholesale step already priced, so the account sees group prices.
    """
    from django.utils import timezone

    from b2b.enum import BusinessProfileStatus, ViesStatus
    from b2b.models import BusinessProfile, CustomerGroup
    from devtools.demo_store import CUSTOMER_GROUPS

    name, discount, minimum = CUSTOMER_GROUPS[0]
    group, _created = CustomerGroup.objects.get_or_create(
        name=name,
        defaults={
            "discount_percent": Decimal(discount),
            "min_order_value": Decimal(minimum),
            "is_active": True,
        },
    )
    street, number, city, zipcode = WHOLESALE_BILLING
    profile, created = BusinessProfile.objects.update_or_create(
        user=user,
        defaults={
            "status": BusinessProfileStatus.APPROVED,
            "customer_group": group,
            "company_name": WHOLESALE_COMPANY,
            "vat_id": WHOLESALE_VAT_ID,
            "tax_office": WHOLESALE_TAX_OFFICE,
            "activity": WHOLESALE_ACTIVITY,
            "billing_street": street,
            "billing_street_number": number,
            "billing_city": city,
            "billing_zipcode": zipcode,
            "vies_status": ViesStatus.UNCHECKED,
        },
    )
    if profile.reviewed_at is None:
        profile.reviewed_at = timezone.now()
        profile.save(update_fields=["reviewed_at"])
    return profile, created


def _seed_account_history(user, pairs, *, with_extras: bool) -> dict[str, int]:
    """Everything that hangs off the account's orders, idempotently.

    Invoices go last: they render a PDF, the one step that can fail for
    a reason of its own, and everything before it should still stand.
    """
    from django.utils import timezone

    now = timezone.now()
    report: dict[str, int] = {}
    for row, order in pairs:
        _bump(report, "shipments", int(_seed_shipment(row, order, now)))
        _bump(report, "redemptions", int(_seed_redemption(order, user)))
        if row.gift_card:
            _bump(report, "gift_card_spend", int(_seed_gift_spend(order)))
    if with_extras:
        orders = {row.days_ago: order for row, order in pairs}
        _bump(report, "points", _seed_points(user, orders))
        _bump(report, "notifications", _seed_notifications(user, orders))
    for row, order in pairs:
        if row.invoice:
            _bump(report, "invoices", int(_seed_invoice(order)))
    return report


def _restore_gift_card() -> int:
    """Put the demo gift card back to its seeded balance and state.

    Guests on the demo store may spend it, and a redemption is a REDEEM
    row on an append-only ledger whose ``order`` goes ``SET_NULL`` when
    the reset deletes the guest's order — so without this the card only
    ever drains, and the next prospect is handed an empty card.

    Through the ledger, the way the domain corrects a balance (the
    admin's "Adjust balance" writes the same row): one ADJUST for the
    difference between the issued value and the RAW ledger sum — not
    ``GiftCard.balance``, which floors at zero and would leave the sum
    off after an over-drawn history. Nothing is overwritten, so the sum
    of the ledger is the balance again, and the card's history still
    shows every spend. The row is locked the way a redemption locks it
    (``plan_redemption(lock=True)``), so a checkout racing the reset
    cannot spend against the balance being restored.

    The state goes back to what ``GiftCardService.issue`` gave it:
    active, a fresh validity window, and no expiry reminder on record.
    A queryset ``update()``, like the rest of this reset, so no card
    save signal can fire. Neither ``GiftCard`` nor
    ``GiftCardTransaction`` has a save receiver today, and the reset
    must stay silent if one is added.

    Only the card the seed issued: ``GIFT_CARD_CODE`` is fixed and
    ``code`` is unique, while every other card carries a code
    ``generate_code`` drew.
    """
    from django.db import transaction
    from django.db.models import Sum

    from giftcard.enum import GiftCardStatus, GiftCardTransactionKind
    from giftcard.models import GiftCard, GiftCardTransaction
    from giftcard.services import GiftCardService

    with transaction.atomic():
        card = (
            GiftCard.objects.select_for_update()
            .filter(code=GIFT_CARD_CODE)
            .first()
        )
        if card is None:
            return 0
        ledger = card.transactions.aggregate(total=Sum("amount"))[
            "total"
        ] or Decimal(0)
        # The value it was ISSUED with, not today's constant (a card
        # holding more than its ``initial_value`` reads as a bug), less
        # what the seeded order spent of it.
        difference = (
            Decimal(card.initial_value.amount) - GIFT_CARD_SPENT - ledger
        )
        if difference:
            GiftCardTransaction.objects.create(
                gift_card=card,
                kind=GiftCardTransactionKind.ADJUST,
                amount=difference,
                description="Demo reset: balance restored",
            )
        GiftCard.objects.filter(pk=card.pk).update(
            status=GiftCardStatus.ACTIVE,
            expires_at=GiftCardService.default_expiry(),
            expiry_reminder_sent_at=None,
        )
    return int(bool(difference))


def seed_demo_account() -> dict[str, int]:
    """Both shared accounts, with everything a signed-in demo needs."""
    report: dict[str, int] = {}

    retail, created = _ensure_user(
        RETAIL_EMAIL, RETAIL_PASSWORD, "Δήμος", "Δοκιμής"
    )
    _bump(report, "retail_created" if created else "retail_unchanged")
    _bump(report, "addresses", _seed_addresses(retail))
    _bump(report, "favourites", _seed_favourites(retail))
    # Before the orders: one of them spends part of this card.
    if _seed_gift_card(retail):
        _bump(report, "gift_card")
    written, pairs = _seed_orders(retail, ORDERS, addresses=ADDRESSES)
    _bump(report, "orders", written)

    wholesale, created = _ensure_user(
        B2B_EMAIL, B2B_PASSWORD, "Χονδρική", "Επίδειξη"
    )
    _bump(report, "b2b_created" if created else "b2b_unchanged")
    _bump(
        report,
        "b2b_addresses",
        _seed_addresses(wholesale, WHOLESALE_ADDRESSES),
    )
    profile, created = _seed_business_profile(wholesale)
    _bump(report, "b2b_profile" if created else "b2b_profile_unchanged")
    written, b2b_pairs = _seed_orders(
        wholesale,
        WHOLESALE_ORDERS,
        addresses=WHOLESALE_ADDRESSES,
        profile=profile,
    )
    _bump(report, "b2b_orders", written)

    for key, value in _seed_account_history(
        retail, pairs, with_extras=True
    ).items():
        _bump(report, key, value)
    for key, value in _seed_account_history(
        wholesale, b2b_pairs, with_extras=False
    ).items():
        _bump(report, f"b2b_{key}", value)
    return report


def reset_demo_account() -> dict[str, int]:
    """Put both accounts back the way the seed left them.

    Everything a visitor can create while signed in is removed first —
    see ``_VISITOR_OWNED`` — and so is every guest checkout placed before
    this run (``_wipe_guest_checkouts``); then the fixtures are rebuilt,
    the catalogue's stock is put back and the demo gift card is
    returned to its seeded balance (``_restore_gift_card``).
    Refuses to run on a schema that is not flagged ``is_demo``. The
    password is restored too: the adapter refuses a change through the
    API, but a reset that could not restore it would be one bug away
    from a store nobody can sign into.
    """
    from django.contrib.auth import get_user_model
    from django.utils import timezone

    from devtools.demo_store import _current_tenant_is_demo

    # The wipe below deletes customer orders. The fanout only ever sends
    # ``is_demo`` schemas here (``tenant/tasks.py``), but a reset that
    # ran against a real merchant — a hand-run task, a wrong schema —
    # would delete that merchant's guest checkouts, so the store is
    # checked again at the point of deletion.
    if not _current_tenant_is_demo():
        return {"skipped_not_a_demo_tenant": 1}

    # Everything a visitor placed BEFORE this run; nothing that lands
    # while the reset is working.
    started_at = timezone.now()
    report: dict[str, int] = {}
    user_model = get_user_model()
    emails = (RETAIL_EMAIL, B2B_EMAIL)

    for email in emails:
        user = user_model.objects.filter(email=email).first()
        if user is None:
            continue
        _bump(report, "wiped", _wipe_visitor_data(user))

    for key, value in _wipe_guest_checkouts(before=started_at).items():
        _bump(report, key, value)
    _bump(report, "invoice_counters_rewound", _rewind_invoice_counters())

    seeded = seed_demo_account()
    for key, value in seeded.items():
        report[f"seeded_{key}"] = value
    _bump(report, "stock_restored", _restore_demo_stock())
    _bump(report, "gift_card_restored", _restore_gift_card())
    return report


def _wipe_guest_checkouts(*, before) -> dict[str, int]:
    """Remove the guest checkouts visitors placed on the demo store.

    A demo store invites strangers to walk the checkout, and a guest
    order is not under either shared account, so the account wipe never
    reached it — prod demo order #1 (2026-09-22) sat PENDING for good,
    because the demo store has no carrier to advance it and the
    auto-cancel only closes online payments.

    Not through ``OrderService.cancel_order``: that is a customer-facing
    cancellation — it emails the guest "your order was canceled",
    cascades to the carrier and pushes the change to the agent gateway —
    for an order that is about to stop existing. What a cancel must
    ALSO do is kept: open reservations are released
    (``StockManager.release_reservation``) and the stock the order
    physically took — its net DECREMENT / INCREMENT in ``StockLog``, the
    sum ``cancel_order`` restores — goes back, so a product outside the
    seeded catalogue does not drain either (``_restore_demo_stock``
    resets only the catalogue's quantities).

    The stock goes back with a queryset ``update()`` plus the INCREMENT
    ``StockLog`` row ``StockManager.increment_stock`` would write — not
    through ``increment_stock`` itself. That method saves the product,
    and the product's history signal (``product/signals.py``) turns a
    0 → positive stock into ``product_back_in_stock``, which emails every
    restock subscriber and pushes a live notification to favouriters;
    the save also queues a recommendation recompute. A reset that must
    be silent cannot go through it — ``_restore_demo_stock`` uses
    ``update()`` for the same reason.

    Then the rows go with ``hard_delete``: items, history and carrier
    shipments cascade; the audit trails that ``SET_NULL`` (stock log,
    webhook and analytics events) keep their rows. Coupon redemptions
    are deleted so the demo coupons' usage limits refill, and an invoice
    is removed first because ``Invoice.order`` is ``PROTECT``.
    """
    from django.db import transaction
    from django.db.models import F, Sum

    from order.exceptions import StockReservationError
    from order.models.order import Order
    from order.models.stock_log import StockLog
    from order.models.stock_reservation import StockReservation
    from order.stock import StockManager
    from product.models import Product
    from promotion.models.redemption import PromotionRedemption

    guests = Order.objects.all_with_deleted().filter(
        user__isnull=True, created_at__lt=before
    )
    orders = list(guests.values_list("id", "metadata"))
    if not orders:
        return {}
    order_ids = [order_id for order_id, _metadata in orders]

    reservation_ids = set(
        StockReservation.objects.filter(
            order_id__in=order_ids, consumed=False
        ).values_list("id", flat=True)
    )
    for _order_id, metadata in orders:
        reservation_ids.update(
            (metadata or {}).get("stock_reservation_ids") or []
        )
    for reservation_id in sorted(reservation_ids):
        try:
            StockManager.release_reservation(reservation_id)
        except StockReservationError:
            # Already consumed or expired-and-cleaned: nothing held.
            continue

    restored = 0
    taken = (
        StockLog.objects.filter(
            order_id__in=order_ids,
            operation_type__in=(
                StockLog.OPERATION_DECREMENT,
                StockLog.OPERATION_INCREMENT,
            ),
        )
        .values("order_id", "product_id")
        .annotate(net=Sum("quantity_delta"))
    )
    for row in taken:
        quantity = -row["net"]
        if quantity <= 0:
            continue
        with transaction.atomic():
            before_stock = (
                Product.objects.select_for_update()
                .values_list("stock", flat=True)
                .get(pk=row["product_id"])
            )
            Product.objects.filter(pk=row["product_id"]).update(
                stock=F("stock") + quantity
            )
            StockLog.objects.create(
                product_id=row["product_id"],
                order_id=row["order_id"],
                operation_type=StockLog.OPERATION_INCREMENT,
                quantity_delta=quantity,
                stock_before=before_stock,
                stock_after=before_stock + quantity,
                reason=f"Demo reset: guest order {row['order_id']} cleared",
            )
        restored += quantity

    PromotionRedemption.objects.filter(order_id__in=order_ids).delete()
    _delete_invoices(order_ids)
    guests.hard_delete()
    return {"guest_orders": len(order_ids), "guest_stock_restored": restored}


def _delete_invoices(order_ids) -> int:
    """Remove the invoices of these orders, rows AND their PDFs.

    ``Invoice.order`` is ``PROTECT``, so they have to go before the
    orders do. Deleting the row alone leaves the PDF in the tenant's
    private storage, and with the counter rewound the next invoice would
    be written under the same name and stored beside it with a suffix:
    the volume would gain a file for every invoice, every night.
    """
    from order.models.invoice import Invoice

    invoices = list(Invoice.objects.filter(order_id__in=order_ids))
    for invoice in invoices:
        if invoice.has_document():
            invoice.document_file.delete(save=False)
    Invoice.objects.filter(pk__in=[i.pk for i in invoices]).delete()
    return len(invoices)


def _rewind_invoice_counters() -> int:
    """Hand the invoice numbers a reset freed back to the counter.

    Invoice numbers are gapless by law, which is why the counter is
    never derived from a row count. On a demo store the fixture invoices
    are deleted and reissued every night, so without this the sequence
    would climb by the fixtures' count forever. The counter goes back to
    one past the highest number still on file, and only ever on a store
    flagged ``is_demo``.
    """
    from order.models.invoice import (
        INVOICE_NUMBER_FORMAT,
        Invoice,
        InvoiceCounter,
    )

    rewound = 0
    for counter in InvoiceCounter.objects.all():
        prefix = INVOICE_NUMBER_FORMAT.split("{number")[0].format(
            year=counter.year
        )
        highest = max(
            (
                int(number.removeprefix(prefix))
                for number in Invoice.objects.filter(
                    invoice_number__startswith=prefix
                ).values_list("invoice_number", flat=True)
            ),
            default=0,
        )
        if counter.next_number != highest + 1:
            counter.next_number = highest + 1
            counter.save(update_fields=["next_number"])
            rewound += 1
    return rewound


def _wipe_visitor_data(user) -> int:
    """Delete what a visitor could have created under this account."""
    from allauth.mfa.models import Authenticator
    from knox.models import AuthToken

    from blog.models.comment import BlogComment
    from cart.models import Cart
    from loyalty.models.transaction import PointsTransaction
    from notification.models import Notification, NotificationUser
    from order.models.order import Order
    from product.models.review import ProductReview
    from promotion.models.redemption import PromotionRedemption

    removed = 0
    orders = Order.objects.all_with_deleted().filter(user=user)
    # Rows that point at the orders go first. Invoices are ``PROTECT``;
    # redemptions would only be orphaned (``SET_NULL``) and keep counting
    # against a coupon's usage limit for ever.
    removed += PromotionRedemption.objects.filter(user=user).delete()[0]
    removed += _delete_invoices(list(orders.values_list("pk", flat=True)))
    # Orders next: points transactions reference them. ``hard_delete``
    # over ``all_with_deleted``: ``Order``'s queryset ``delete()`` only
    # soft-deletes, so every reset left the previous night's four
    # fixtures behind as hidden rows (production demo schema: 12 of 17
    # orders on 2026-09-25).
    removed += orders.hard_delete()[0]
    removed += PointsTransaction.objects.filter(user=user).delete()[0]
    removed += ProductReview.objects.filter(user=user).delete()[0]
    removed += BlogComment.objects.filter(user=user).delete()[0]
    removed += Cart.objects.filter(user=user).delete()[0]
    # A notification addressed to this account alone goes with it; one
    # also delivered to other people is theirs too and stays.
    notification_ids = list(
        NotificationUser.objects.filter(user=user).values_list(
            "notification_id", flat=True
        )
    )
    removed += NotificationUser.objects.filter(user=user).delete()[0]
    removed += (
        Notification.objects.filter(pk__in=notification_ids)
        .exclude(notification_users__isnull=False)
        .delete()[0]
    )
    # A second factor enrolled by a visitor would lock everyone out. The
    # middleware refuses enrolment, so this is the belt to that brace.
    removed += Authenticator.objects.filter(user=user).delete()[0]
    removed += AuthToken.objects.filter(user=user).delete()[0]
    return removed


def _restore_demo_stock() -> int:
    """Put every demo product's stock back to its seeded quantity.

    A cash-on-delivery checkout consumes stock, so a demo left running
    drifts towards "out of stock" on exactly the products a prospect is
    most likely to try.
    """
    from devtools.demo_catalogue import PRODUCTS
    from product.models import Product

    restored = 0
    for row in PRODUCTS:
        updated = (
            Product.objects.filter(slug=row.slug)
            .exclude(stock=row.stock)
            .update(stock=row.stock)
        )
        restored += updated
    return restored


def showcase_settings() -> dict[str, Any]:
    """The `DEMO_ACCOUNT_*` rows, for a tenant flagged ``is_demo``.

    Kept out of ``DEMO_SETTINGS`` on purpose: writing these publishes
    two passwords through ``/api/v1/settings/public``, which is correct
    for a showcase and wrong everywhere else.
    """
    return {
        "DEMO_ACCOUNT_ENABLED": True,
        "DEMO_ACCOUNT_EMAIL": RETAIL_EMAIL,
        "DEMO_ACCOUNT_PASSWORD": RETAIL_PASSWORD,
        "DEMO_ACCOUNT_B2B_EMAIL": B2B_EMAIL,
        "DEMO_ACCOUNT_B2B_PASSWORD": B2B_PASSWORD,
    }
