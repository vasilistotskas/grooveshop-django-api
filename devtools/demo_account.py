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

Orders are written with `bulk_create` and backdated with `update()`
rather than through `OrderService` or `save()`. A seeded order must not
send a confirmation email, must not move stock, and must not enter the
state machine — it is a fixture, not a sale.
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
    #: How the shopper paid (``PaySettlement``). The fixture takes the
    #: store's first pay way with that settlement, so the order's
    #: ``pay_way`` and its payment fields always agree; with no such pay
    #: way the row is skipped, like a row whose products are missing.
    settlement: str
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

#: Each row is a state a real order can hold. A paid order that was
#: delivered is COMPLETED — ``complete_paid_delivered_orders`` moves any
#: DELIVERED + paid order there within the hour, so a DELIVERED fixture
#: only ever re-entered the state machine. The collect-on-delivery
#: parcel went to a BoxNow locker, so it settles at the locker terminal.
ORDERS: tuple[OrderRow, ...] = (
    OrderRow(
        status="COMPLETED",
        payment_status="COMPLETED",
        settlement="online",
        days_ago=21,
        items=(
            ("demo-powerbank-20k", 1),
            ("demo-cable-usbc-braided-black", 2),
        ),
        tracking_number="DEMO0000000021",
        shipping_carrier="acs",
    ),
    OrderRow(
        status="SHIPPED",
        payment_status="PENDING",
        settlement="carrier_terminal",
        days_ago=5,
        items=(("demo-earbuds-black", 1),),
        tracking_number="DEMO0000000005",
        shipping_carrier="boxnow",
        customer_notes="Παράδοση μετά τις 17:00 παρακαλώ.",
    ),
    OrderRow(
        status="PROCESSING",
        payment_status="COMPLETED",
        settlement="online",
        days_ago=1,
        items=(("demo-charger-gan-65w", 1), ("demo-case-clear", 1)),
    ),
    OrderRow(
        status="CANCELED",
        payment_status="CANCELED",
        settlement="online",
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

    Never ``OrderService``, and never ``save()`` either: ``bulk_create``
    sends no ``post_save`` (Django's documented bulk_create caveat), so
    ``handle_order_post_save`` never fires ``order_created`` — which is
    what mailed an order confirmation and a merchant new-order email for
    every fixture on every nightly reset, pushed a WebSocket toast and
    an agent-gateway event, while the docstring above promised none of
    it. These are history for an order-list page to render, not sales.

    ``bulk_create`` also skips ``Order.save()``, so the two fields it
    derives are written here: ``pay_way_key`` through the model's own
    snapshot, and ``paid_amount`` — what the shopper owed, as on a real
    order — once the lines exist to total.
    """
    from django.utils import timezone

    from country.models import Country
    from order.models.item import OrderItem
    from order.models.order import Order
    from pay_way.models import PayWay
    from product.models import Product

    country = Country.objects.filter(alpha_2="GR").first()
    main = ADDRESSES[0]
    now = timezone.now()
    written = 0

    for row in ORDERS:
        if Order.objects.filter(
            user=user, metadata__demo_seed=row.days_ago
        ).exists():
            continue
        products = [
            (Product.objects.filter(slug=slug).first(), quantity)
            for slug, quantity in row.items
        ]
        products = [(p, q) for p, q in products if p is not None]
        pay_way = (
            PayWay.objects.filter(settlement=row.settlement)
            .order_by("id")
            .first()
        )
        if not products or pay_way is None:
            continue

        order = Order(
            user=user,
            email=user.email,
            first_name=main.first_name,
            last_name=main.last_name,
            street=main.street,
            street_number=main.street_number,
            city=main.city,
            zipcode=main.zipcode,
            floor=main.floor,
            country=country,
            pay_way=pay_way,
            status=row.status,
            payment_status=row.payment_status,
            # The gateway that took the money (docs/order-system.md §1),
            # so only a paid online order names one.
            payment_method=(
                pay_way.provider_code
                if row.payment_status == "COMPLETED"
                and row.settlement == "online"
                else ""
            ),
            tracking_number=row.tracking_number,
            shipping_carrier=row.shipping_carrier,
            customer_notes=row.customer_notes,
            metadata={"demo_seed": row.days_ago},
        )
        order._snapshot_pay_way_key()
        [order] = Order.objects.bulk_create([order])

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
        placed_at = now - timedelta(days=row.days_ago)
        # `update()`: bulk_create stamps today into the auto_now columns.
        Order.objects.filter(pk=order.pk).update(
            paid_amount=order.calculate_order_total_amount(),
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
        # The value it was ISSUED with, not today's constant: a card
        # holding more than its ``initial_value`` reads as a bug.
        difference = Decimal(card.initial_value.amount) - ledger
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
    from order.models.invoice import Invoice
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
    Invoice.objects.filter(order_id__in=order_ids).delete()
    guests.hard_delete()
    return {"guest_orders": len(order_ids), "guest_stock_restored": restored}


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
    # Orders first: points transactions reference them. ``hard_delete``
    # over ``all_with_deleted``: ``Order``'s queryset ``delete()`` only
    # soft-deletes, so every reset left the previous night's four
    # fixtures behind as hidden rows (production demo schema: 12 of 17
    # orders on 2026-09-25).
    removed += (
        Order.objects.all_with_deleted().filter(user=user).hard_delete()[0]
    )
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
