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
    addresses, favourites, points — is wiped and rebuilt by
    `reset_demo_account`, which the nightly task runs.

Orders are written with the ORM directly and backdated with `update()`
rather than through `OrderService`. A seeded order must not send a
confirmation email, must not move stock, and must not enter the state
machine — it is a fixture, not a sale.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from core.enum import FloorChoicesEnum

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


@dataclass(frozen=True)
class OrderRow:
    """One fixture order.

    ``items`` is ``(product_slug, quantity)``; the price is taken from
    the product at seed time so the order never disagrees with the
    catalogue a prospect is looking at.
    """

    status: str
    payment_status: str
    payment_method: str
    days_ago: int
    items: tuple[tuple[str, int], ...]
    tracking_number: str = ""
    shipping_carrier: str = ""
    customer_notes: str = ""


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
)

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

ORDERS: tuple[OrderRow, ...] = (
    OrderRow(
        status="DELIVERED",
        payment_status="COMPLETED",
        payment_method="Κάρτα",
        days_ago=21,
        items=(
            ("demo-powerbank-20k", 1),
            ("demo-cable-usbc-braided-black", 2),
        ),
        tracking_number="DEMO0000000021",
        shipping_carrier="ACS",
    ),
    OrderRow(
        status="SHIPPED",
        payment_status="PENDING",
        payment_method="Αντικαταβολή",
        days_ago=5,
        items=(("demo-earbuds-black", 1),),
        tracking_number="DEMO0000000005",
        shipping_carrier="BOX NOW",
        customer_notes="Παράδοση μετά τις 17:00 παρακαλώ.",
    ),
    OrderRow(
        status="PROCESSING",
        payment_status="COMPLETED",
        payment_method="Κάρτα",
        days_ago=1,
        items=(("demo-charger-gan-65w", 1), ("demo-case-clear", 1)),
    ),
    OrderRow(
        status="CANCELED",
        payment_status="CANCELED",
        payment_method="Κάρτα",
        days_ago=40,
        items=(("demo-glass-privacy", 1),),
    ),
)

#: ``(transaction_type, points, description)`` — a ledger a prospect can
#: read: a welcome bonus, earnings from the delivered order, and a
#: redemption.
POINTS: tuple[tuple[str, int, str], ...] = (
    ("BONUS", 100, "Καλωσόρισμα"),
    ("EARN", 620, "Πόντοι από παραγγελίες"),
    ("REDEEM", -200, "Εξαργύρωση σε έκπτωση"),
)

GIFT_CARD_CODE = "DEMO-GIFT-25"
GIFT_CARD_AMOUNT = Decimal("25.00")

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


def _seed_addresses(user) -> int:
    from country.models import Country
    from user.models.address import UserAddress

    country = Country.objects.filter(alpha_2="GR").first()
    written = 0
    for row in ADDRESSES:
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
                "country": country,
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


def _seed_orders(user) -> int:
    """Fixture orders, written straight to the ORM and backdated.

    Never ``OrderService``: that sends the confirmation email, reserves
    and decrements stock and drives the state machine. These are
    history for an order-list page to render, not sales.
    """
    from django.utils import timezone

    from country.models import Country
    from order.models.item import OrderItem
    from order.models.order import Order
    from pay_way.models import PayWay
    from product.models import Product

    country = Country.objects.filter(alpha_2="GR").first()
    pay_way = PayWay.objects.filter(active=True).order_by("id").first()
    main = ADDRESSES[0]
    now = timezone.now()
    written = 0

    for row in ORDERS:
        products = [
            (Product.objects.filter(slug=slug).first(), quantity)
            for slug, quantity in row.items
        ]
        products = [(p, q) for p, q in products if p is not None]
        if not products:
            continue

        placed_at = now - timedelta(days=row.days_ago)
        order, created = Order.objects.get_or_create(
            user=user,
            metadata__demo_seed=row.days_ago,
            defaults={
                "email": user.email,
                "first_name": main.first_name,
                "last_name": main.last_name,
                "street": main.street,
                "street_number": main.street_number,
                "city": main.city,
                "zipcode": main.zipcode,
                "floor": main.floor,
                "country": country,
                "pay_way": pay_way,
                "status": row.status,
                "payment_status": row.payment_status,
                "payment_method": row.payment_method,
                "tracking_number": row.tracking_number,
                "shipping_carrier": row.shipping_carrier,
                "customer_notes": row.customer_notes,
                "metadata": {"demo_seed": row.days_ago},
            },
        )
        if not created:
            continue

        OrderItem.objects.bulk_create(
            [
                OrderItem(
                    order=order,
                    product=product,
                    quantity=quantity,
                    price=product.final_price,
                )
                for product, quantity in products
            ]
        )
        # `update()` rather than `save()`: auto_now on the timestamp
        # mixin would stamp today over every backdate.
        Order.objects.filter(pk=order.pk).update(
            created_at=placed_at,
            updated_at=placed_at,
            status_updated_at=placed_at,
        )
        written += 1
    return written


def _seed_points(user) -> int:
    from loyalty.models.transaction import PointsTransaction

    written = 0
    for transaction_type, points, description in POINTS:
        _row, created = PointsTransaction.objects.get_or_create(
            user=user,
            transaction_type=transaction_type,
            description=description,
            defaults={"points": points},
        )
        written += int(created)

    total = sum(points for _type, points, _description in POINTS)
    user.total_xp = max(total, 0)
    user.save(update_fields=["total_xp"])
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


def seed_demo_account() -> dict[str, int]:
    """Both shared accounts, with everything a signed-in demo needs."""
    report: dict[str, int] = {}

    retail, created = _ensure_user(
        RETAIL_EMAIL, RETAIL_PASSWORD, "Δήμος", "Δοκιμής"
    )
    _bump(report, "retail_created" if created else "retail_unchanged")
    _bump(report, "addresses", _seed_addresses(retail))
    _bump(report, "favourites", _seed_favourites(retail))
    _bump(report, "orders", _seed_orders(retail))
    _bump(report, "points", _seed_points(retail))
    if _seed_gift_card(retail):
        _bump(report, "gift_card")

    wholesale, created = _ensure_user(
        B2B_EMAIL, B2B_PASSWORD, "Χονδρική", "Επίδειξη"
    )
    _bump(report, "b2b_created" if created else "b2b_unchanged")
    _bump(report, "b2b_addresses", _seed_addresses(wholesale))

    return report


def reset_demo_account() -> dict[str, int]:
    """Put both accounts back the way the seed left them.

    Everything a visitor can create while signed in is removed first —
    see ``_VISITOR_OWNED`` — and then the fixtures are rebuilt. The
    password is restored too: the adapter refuses a change through the
    API, but a reset that could not restore it would be one bug away
    from a store nobody can sign into.
    """
    from django.contrib.auth import get_user_model

    report: dict[str, int] = {}
    user_model = get_user_model()
    emails = (RETAIL_EMAIL, B2B_EMAIL)

    for email in emails:
        user = user_model.objects.filter(email=email).first()
        if user is None:
            continue
        _bump(report, "wiped", _wipe_visitor_data(user))

    seeded = seed_demo_account()
    for key, value in seeded.items():
        report[f"seeded_{key}"] = value
    _bump(report, "stock_restored", _restore_demo_stock())
    return report


def _wipe_visitor_data(user) -> int:
    """Delete what a visitor could have created under this account."""
    from allauth.mfa.models import Authenticator
    from knox.models import AuthToken

    from blog.models.comment import BlogComment
    from cart.models import Cart
    from loyalty.models.transaction import PointsTransaction
    from notification.models import NotificationUser
    from order.models.order import Order
    from product.models.review import ProductReview

    removed = 0
    # Orders first: points transactions reference them.
    removed += Order.objects.filter(user=user).delete()[0]
    removed += PointsTransaction.objects.filter(user=user).delete()[0]
    removed += ProductReview.objects.filter(user=user).delete()[0]
    removed += BlogComment.objects.filter(user=user).delete()[0]
    removed += Cart.objects.filter(user=user).delete()[0]
    removed += NotificationUser.objects.filter(user=user).delete()[0]
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
