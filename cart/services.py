from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

from cart.agent_token import (
    CartTokenNotPermitted,
    cart_token_from_request,
    resolve_cart_token,
)
from cart.models import Cart, CartAttribution, CartItem
from order.attribution import ClassifiedAttribution
from order.models.stock_reservation import StockReservation

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from django.http import HttpRequest
    from rest_framework.request import Request

    from product.models.product import Product


class CartServiceInitException(Exception):
    pass


def cart_uuid_from_request(request: Request | HttpRequest) -> uuid.UUID | None:
    """The cart the caller is addressing, from the ``X-Cart-Id`` header.

    Carts are addressed by their unguessable UUID, never the sequential
    PK — otherwise any anonymous caller could enumerate other guests'
    carts by incrementing an integer header (IDOR). Anything that is
    not a valid UUID reads as "no cart". The storefront proxy sends the
    header on every cart-scoped call, including the recommendation
    feedback events, so the same parse serves both.
    """
    if hasattr(request, "META"):
        raw = request.META.get("HTTP_X_CART_ID")
    else:
        raw = request.headers.get("X-Cart-Id")
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError, TypeError, AttributeError:
        return None


def cart_item_pairs(
    request: Request | HttpRequest,
) -> list[tuple[Product, int]] | None:
    """``(product, quantity)`` for the cart the request addresses.

    Plus the free gifts the cart is entitled to (``gift_pairs``).
    ``None`` when it addresses none (no header, an unknown cart, a guest
    without one); an empty list for a cart with no lines. The shipping
    endpoints that ask whether the cart fits a carrier's parcels read it
    through here, so they find the cart exactly as the cart endpoints do.
    They are reads that reveal only fit, like ``GET /cart``, so a
    ``X-Cart-Token`` is accepted (the signed-in shopper's chat): it wins
    over ``X-Cart-Id`` and an invalid one is a 401, never a guest cart.
    """
    from promotion.services import PromotionEngine

    service = CartService(request, accepts_cart_token=True)
    cart = service.cart
    if cart is None:
        return None
    # The gifts the cart is entitled to ride in the parcel too; the
    # preview evaluation is the one the cart read runs.
    promo_result = PromotionEngine.evaluate(cart, user=service.owner)
    return [
        (item.product, item.quantity) for item in cart.get_items()
    ] + PromotionEngine.gift_pairs(promo_result)


class CartAttributionService:
    """The one place a cart's first-touch source is decided.

    Every path that can take a cart from no lines to some goes through
    ``capture``, called BEFORE it writes the line: a new cart's first
    add, a refill after the shopper emptied it, a reorder, and the
    guest-to-account merge.
    """

    @classmethod
    def capture(
        cls,
        cart: Cart,
        touch: ClassifiedAttribution,
        *,
        touched_at: datetime | None = None,
    ) -> CartAttribution | None:
        """Record *touch* as the first touch of *cart*, or keep the one
        it has.

        ``touched_at`` is when *touch* was first recorded, given only
        when it is an earlier recording being carried over (the merge
        hands over the guest cart's). Without it the touch is happening
        now.

        - An empty cart takes *touch*, replacing whatever an earlier
          fill left: a cart refilled after being cleared starts over.
        - A cart that holds lines keeps its source. A touch happening
          now never replaces it, so the second add is a no-op.
        - A carried-over touch still wins over a later one, so a merge
          keeps the earliest source, and over no source at all.
        """
        current = CartAttribution.objects.filter(pk=cart.pk).first()
        holds_lines = CartItem.objects.filter(cart_id=cart.pk).exists()
        if holds_lines and (
            touched_at is None
            or (current is not None and current.created_at <= touched_at)
        ):
            return current
        at = touched_at or timezone.now()
        attribution, created = CartAttribution.objects.update_or_create(
            cart=cart,
            defaults={
                "source_type": touch.source_type,
                "source": touch.source,
                "medium": touch.medium,
                "campaign": touch.campaign,
                "referrer_host": touch.referrer_host,
                "landing_path": touch.landing_path,
                # An insert stamps ``created_at`` itself; only a
                # replacement honours this one.
                "created_at": at,
            },
        )
        if created and touched_at is not None:
            CartAttribution.objects.filter(pk=cart.pk).update(created_at=at)
            attribution.created_at = at
        return attribution


class CartService:
    """Resolves the cart a request addresses.

    ``accepts_cart_token`` says whether the caller's endpoint may be
    driven by an ``X-Cart-Token`` (see ``cart.agent_token``). A token
    wins over ``X-Cart-Id``, which is then ignored. A token is refused,
    never replaced by another cart: an invalid one raises
    ``CartTokenInvalid``, and one sent where it is not accepted raises
    ``CartTokenNotPermitted``.
    """

    def __init__(
        self,
        request: Request | HttpRequest,
        *,
        accepts_cart_token: bool = False,
    ):
        if not request:
            raise CartServiceInitException(
                "Request must be provided to initialize CartService."
            )

        self.request = request
        self.accepts_cart_token = accepts_cart_token
        self.cart: Cart | None = None
        self.cart_items: list[CartItem] = []

        self._extract_cart_info()

        self._initialize_cart()

    def _extract_cart_info(self):
        self.cart_id: uuid.UUID | None = cart_uuid_from_request(self.request)
        self.token_cart: Cart | None = None
        token = cart_token_from_request(self.request)
        if token:
            if not self.accepts_cart_token:
                raise CartTokenNotPermitted
            self.token_cart = resolve_cart_token(token)

    def __str__(self):
        return f"Cart {self.cart.user if self.cart and self.cart.user else 'Anonymous'}"

    def __len__(self):
        return self.cart.total_items if self.cart else 0

    def __iter__(self):
        if self.cart:
            yield from self.cart_items

    @property
    def owner(self):
        """Whom the cart belongs to as far as this request is concerned.

        The signed-in user, or the owner of the cart a cart token
        names: that request is anonymous yet acts on a signed-in
        cart, whose pricing (a wholesale buyer's) and coupons must
        follow the owner, never ``request.user``. ``None`` for guests.
        """
        if self.token_cart is not None:
            return self.token_cart.user
        user = self.request.user
        return user if user.is_authenticated else None

    def _initialize_cart(self):
        if self.token_cart is not None:
            self.cart = self.get_existing_cart()
        elif self.request.user.is_authenticated and self.cart_id:
            guest_cart = (
                Cart.objects.guest_carts().filter(uuid=self.cart_id).first()
            )
            if guest_cart:
                self.cart = self.get_or_create_cart()
            else:
                self.cart = self.get_existing_cart()
        else:
            self.cart = self.get_existing_cart()

        self.cart_items = self.cart.get_items() if self.cart else []
        if self.cart:
            self.cart.refresh_last_activity()

    def _bind_pricing(self, cart):
        """Attach the wholesale-pricing context to a cart this service
        hands out.

        Both materialization methods below route through here, so EVERY
        instance a view receives — retrieve/update/destroy, coupon
        apply/remove, ``create_payment_intent`` — carries the same
        bound prices, and the preview totals always match the amount
        the payment provider is asked to charge. (The views' own
        ``for_detail`` reloads transplant the context off these
        instances rather than re-resolving.) No-op for guests,
        non-approved users and disabled tenants.
        """
        if cart is not None:
            from b2b.services import B2BPricingService

            B2BPricingService.bind_cart(cart, self.owner)
        return cart

    def get_existing_cart(self):
        if self.token_cart is not None:
            return self._bind_pricing(self.token_cart)

        user = self.request.user

        if user.is_authenticated:
            cart = Cart.objects.filter(user=user).first()

            if cart and self.cart_id:
                guest_cart = (
                    Cart.objects.guest_carts().filter(uuid=self.cart_id).first()
                )

                if guest_cart:
                    self.merge_carts(guest_cart, cart)

            return self._bind_pricing(cart)
        else:
            if self.cart_id:
                return self._bind_pricing(
                    Cart.objects.guest_carts().filter(uuid=self.cart_id).first()
                )

            return None

    def get_or_create_cart(self):
        if self.token_cart is not None:
            return self._bind_pricing(self.token_cart)

        user = self.request.user

        if user.is_authenticated:
            cart, _created = Cart.objects.get_or_create(user=user)

            if self.cart_id:
                guest_cart = (
                    Cart.objects.guest_carts().filter(uuid=self.cart_id).first()
                )

                if guest_cart:
                    self.merge_carts(guest_cart, cart)

            return self._bind_pricing(cart)
        else:
            if self.cart_id:
                cart = (
                    Cart.objects.guest_carts().filter(uuid=self.cart_id).first()
                )
                if cart:
                    return self._bind_pricing(cart)

            return Cart.objects.create(user=None)

    @transaction.atomic
    def merge_carts(self, source_cart: Cart, target_cart: Cart | None = None):
        if not target_cart:
            target_cart = self.cart

        if not target_cart or not source_cart:
            return

        if target_cart.id == source_cart.id:
            return

        # Before any line moves: whether the target already holds lines
        # is what decides which source the merged cart keeps. A guest
        # cart that was emptied brings no lines, so its source brings
        # nothing either.
        source_touch = CartAttribution.objects.filter(pk=source_cart.pk).first()
        if (
            source_touch is not None
            and CartItem.objects.filter(cart_id=source_cart.pk).exists()
        ):
            CartAttributionService.capture(
                target_cart,
                ClassifiedAttribution(
                    source_type=source_touch.source_type,
                    source=source_touch.source,
                    medium=source_touch.medium,
                    campaign=source_touch.campaign,
                    referrer_host=source_touch.referrer_host,
                    landing_path=source_touch.landing_path,
                ),
                touched_at=source_touch.created_at,
            )

        lines_moved = 0
        lines_combined = 0
        lines_capped = 0

        # ``of=("self",)``: the merge reads each product (stock cap) but
        # never writes one. Without it the join locked every product row
        # of the guest cart in its line order, which a checkout locking
        # the same products in pk order deadlocks with.
        for item in (
            source_cart.items.select_for_update(of=("self",))
            .select_related("product")
            .all()
        ):
            existing_item = (
                CartItem.objects.select_for_update()
                .filter(cart=target_cart, product=item.product)
                .first()
            )

            if existing_item:
                # Cap the merged quantity at available stock so a
                # guest→user merge can't silently stack a line past stock
                # (the login-time analog of the add-to-cart cumulative
                # check). Best-effort UX gate only — the authoritative
                # oversell guard remains StockManager.reserve_stock at
                # checkout. Only caps when stock is positive, so an
                # out-of-stock product's line isn't zeroed mid-merge.
                merged_quantity = existing_item.quantity + item.quantity
                stock = item.product.stock if item.product else 0
                if stock > 0 and merged_quantity > stock:
                    merged_quantity = stock
                    lines_capped += 1
                existing_item.quantity = merged_quantity
                existing_item.save()
                item.delete()
                lines_combined += 1
            else:
                item.cart = target_cart
                item.save()
                lines_moved += 1

        if target_cart == self.cart:
            self.cart_items = self.cart.get_items()

        source_uuid = source_cart.uuid
        # Stock holds are keyed by the CART UUID (`session_id`), and the
        # source cart is about to stop existing. Left behind, the guest's
        # own holds become unreachable AND keep counting against
        # availability: `decrement_stock(respect_reservations=True)`
        # subtracts every active reservation, so the shopper who placed
        # them is the one blocked from checking out — with the last units
        # of a scarce product, by exactly their own quantity. They cannot
        # even be released, since `release_reservations` matches on
        # `reserved_by` (null for a guest) or the CURRENT cart's uuid.
        # So they move with the cart, and gain the owner they now have.
        rehomed = StockReservation.objects.filter(
            session_id=str(source_uuid), consumed=False
        ).update(
            session_id=str(target_cart.uuid),
            reserved_by=target_cart.user,
        )
        source_cart.delete()
        # One line per merge answers "why did my cart change after
        # logging in" without DB archaeology — especially which lines
        # were quantity-capped at stock.
        logger.info(
            "Merged guest cart %s into cart %s: moved=%s combined=%s "
            "capped_at_stock=%s rehomed_reservations=%s",
            source_uuid,
            target_cart.id,
            lines_moved,
            lines_combined,
            lines_capped,
            rehomed,
        )

    def get_cart_by_id(self, cart_uuid: str | uuid.UUID):
        """Return the cart identified by ``cart_uuid`` if the caller owns it.

        Authenticated users may only fetch their own cart. Anonymous
        callers may only fetch the guest cart bound to the X-Cart-Id
        header captured during ``_extract_cart_info``. The lookup uses
        the public UUID
        rather than the integer PK so the namespace is not enumerable.
        """
        try:
            normalized = (
                cart_uuid
                if isinstance(cart_uuid, uuid.UUID)
                else uuid.UUID(str(cart_uuid))
            )
        except ValueError, TypeError:
            return None

        if self.token_cart is not None:
            return (
                Cart.objects.for_detail()
                .filter(uuid=normalized, pk=self.token_cart.pk)
                .first()
            )
        if self.request.user.is_authenticated:
            return (
                Cart.objects.for_detail()
                .filter(uuid=normalized, user=self.request.user)
                .first()
            )
        if self.cart_id == normalized:
            return (
                Cart.objects.for_detail()
                .filter(uuid=normalized, user__isnull=True)
                .first()
            )
        return None

    def get_cart_item(self, product_id: int | None):
        if self.cart:
            return self.cart.items.filter(product_id=product_id).first()
        return None
