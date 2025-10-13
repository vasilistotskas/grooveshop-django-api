from __future__ import annotations

from enum import Enum, unique
from typing import TYPE_CHECKING

from cart.models import Cart, CartItem

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
            self.cart_id = self.request.META.get("HTTP_X_CART_ID")
        else:
            self.cart_id = self.request.headers.get("X-Cart-Id")

        if self.cart_id:
            try:
                self.cart_id = int(self.cart_id)
            except (ValueError, TypeError):
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
            guest_cart = Cart.objects.filter(
                id=self.cart_id, user__isnull=True
            ).first()
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
                guest_cart = Cart.objects.filter(
                    id=self.cart_id, user__isnull=True
                ).first()

                if guest_cart:
                    self.merge_carts(guest_cart, cart)

            return cart
        else:
            if self.cart_id:
                return Cart.objects.filter(
                    id=self.cart_id, user__isnull=True
                ).first()

            return None

    def get_or_create_cart(self):
        user = self.request.user

        if user.is_authenticated:
            cart, created = Cart.objects.get_or_create(user=user)

            if self.cart_id:
                guest_cart = Cart.objects.filter(
                    id=self.cart_id, user__isnull=True
                ).first()

                if guest_cart:
                    self.merge_carts(guest_cart, cart)

            return cart
        else:
            if self.cart_id:
                cart = Cart.objects.filter(
                    id=self.cart_id, user__isnull=True
                ).first()
                if cart:
                    return cart

            return Cart.objects.create(user=None)

    def merge_carts(self, source_cart: Cart, target_cart: Cart = None):
        if not target_cart:
            target_cart = self.cart

        if not target_cart or not source_cart:
            return

        if target_cart.id == source_cart.id:
            return

        for item in source_cart.items.all():
            existing_item = CartItem.objects.filter(
                cart=target_cart, product=item.product
            ).first()

            if existing_item:
                existing_item.quantity += item.quantity
                existing_item.save()
                item.delete()
            else:
                item.cart = target_cart
                item.save()

        if target_cart == self.cart:
            self.cart_items = self.cart.get_items()

        source_cart.delete()

    def clean_cart(self):
        if self.cart:
            self.cart.items.all().delete()
            self.cart_items = []

    def get_cart_by_id(self, cart_id: int):
        return Cart.objects.filter(id=cart_id).first()

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
