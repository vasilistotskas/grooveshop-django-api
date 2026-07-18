from __future__ import annotations

import logging
import uuid
from enum import Enum, unique
from typing import TYPE_CHECKING

from django.db import transaction

from cart.models import Cart, CartItem

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from django.http import HttpRequest
    from rest_framework.request import Request

    from product.models.product import Product


class CartServiceInitException(Exception):
    pass


class CartNotSetException(Exception):
    pass


class InvalidProcessCartOptionException(Exception):
    pass


@unique
class ProcessCartOption(Enum):
    MERGE = "merge"
    CLEAN = "clean"
    KEEP = "keep"


class CartService:
    def __init__(self, request: Request | HttpRequest):
        if not request:
            raise CartServiceInitException(
                "Request must be provided to initialize CartService."
            )

        self.request = request
        self.cart: Cart | None = None
        self.cart_items: list[CartItem] = []

        self._extract_cart_info()

        try:
            self._initialize_cart()
        except CartServiceInitException:
            raise

    def _extract_cart_info(self):
        if hasattr(self.request, "META"):
            raw_cart_id = self.request.META.get("HTTP_X_CART_ID")
        else:
            raw_cart_id = self.request.headers.get("X-Cart-Id")

        # Guest carts are addressed by their unguessable UUID, never the
        # sequential PK — otherwise any anonymous caller can enumerate other
        # guests' carts by incrementing an integer header (IDOR). Reject
        # anything that is not a valid UUID.
        self.cart_id: uuid.UUID | None = None
        if raw_cart_id:
            try:
                self.cart_id = uuid.UUID(str(raw_cart_id))
            except (ValueError, TypeError, AttributeError):
                self.cart_id = None

    def __str__(self):
        return f"Cart {self.cart.user if self.cart and self.cart.user else 'Anonymous'}"

    def __len__(self):
        return self.cart.total_items if self.cart else 0

    def __iter__(self):
        if self.cart:
            yield from self.cart_items

    def _initialize_cart(self):
        if self.request.user.is_authenticated and self.cart_id:
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

    def get_existing_cart(self):
        user = self.request.user

        if user.is_authenticated:
            cart = Cart.objects.filter(user=user).first()

            if cart and self.cart_id:
                guest_cart = (
                    Cart.objects.guest_carts().filter(uuid=self.cart_id).first()
                )

                if guest_cart:
                    self.merge_carts(guest_cart, cart)

            return cart
        else:
            if self.cart_id:
                return (
                    Cart.objects.guest_carts().filter(uuid=self.cart_id).first()
                )

            return None

    def get_or_create_cart(self):
        user = self.request.user

        if user.is_authenticated:
            cart, created = Cart.objects.get_or_create(user=user)

            if self.cart_id:
                guest_cart = (
                    Cart.objects.guest_carts().filter(uuid=self.cart_id).first()
                )

                if guest_cart:
                    self.merge_carts(guest_cart, cart)

            return cart
        else:
            if self.cart_id:
                cart = (
                    Cart.objects.guest_carts().filter(uuid=self.cart_id).first()
                )
                if cart:
                    return cart

            return Cart.objects.create(user=None)

    @transaction.atomic
    def merge_carts(self, source_cart: Cart, target_cart: Cart | None = None):
        if not target_cart:
            target_cart = self.cart

        if not target_cart or not source_cart:
            return

        if target_cart.id == source_cart.id:
            return

        lines_moved = 0
        lines_combined = 0
        lines_capped = 0

        for item in (
            source_cart.items.select_for_update()
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
        source_cart.delete()
        # One line per merge answers "why did my cart change after
        # logging in" without DB archaeology — especially which lines
        # were quantity-capped at stock.
        logger.info(
            "Merged guest cart %s into cart %s: moved=%s combined=%s "
            "capped_at_stock=%s",
            source_uuid,
            target_cart.id,
            lines_moved,
            lines_combined,
            lines_capped,
        )

    def clean_cart(self):
        if self.cart:
            self.cart.items.all().delete()
            self.cart_items = []

    def get_cart_by_id(self, cart_id: int):
        return Cart.objects.for_detail().filter(id=cart_id).first()

    def get_cart_item(self, product_id: int | None):
        if self.cart:
            return self.cart.items.filter(product_id=product_id).first()
        return None

    def create_cart_item(self, product: Product, quantity: int):
        if not self.cart:
            raise CartNotSetException("Cart is not set.")

        existing_item = CartItem.objects.filter(
            cart=self.cart, product=product
        ).first()

        if existing_item:
            existing_item.quantity += quantity
            existing_item.save()
            cart_item = existing_item
        else:
            cart_item = CartItem.objects.create(
                cart=self.cart, product=product, quantity=quantity
            )

        self.cart_items = self.cart.get_items()
        return cart_item

    def update_cart_item(self, product_id: int, quantity: int):
        if not self.cart:
            raise CartNotSetException("Cart is not set.")
        cart_item = self.cart.items.get(product_id=product_id)
        cart_item.quantity = quantity
        cart_item.save()
        self.cart_items = self.cart.get_items()
        return cart_item

    def delete_cart_item(self, product_id: int):
        if not self.cart:
            raise CartNotSetException("Cart is not set.")
        self.cart.items.filter(product_id=product_id).delete()
        self.cart_items = self.cart.get_items()
