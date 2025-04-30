from __future__ import annotations

import uuid
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
        try:
            self._initialize_cart()
        except CartServiceInitException:
            raise

    def __str__(self):
        return f"Cart {self.cart.user if self.cart and self.cart.user else 'Anonymous'}"

    def __len__(self):
        return self.cart.total_items if self.cart else 0

    def __iter__(self):
        if self.cart:
            yield from self.cart_items

    def _initialize_cart(self):
        self.cart = self.get_or_create_cart()
        self.cart_items = self.cart.get_items() if self.cart else []
        if self.cart:
            self.cart.refresh_last_activity()

    def process_cart(self, option: ProcessCartOption):
        pre_login_cart_id = self.request.session.get("pre_log_in_cart_id")
        if isinstance(pre_login_cart_id, int | str):
            pre_login_cart_id = int(pre_login_cart_id)

        if not pre_login_cart_id and self.request.user.is_authenticated:
            cart_id = self.request.session.get("cart_id")
            if cart_id:
                cart = self.get_cart_by_id(cart_id)
                if cart and not cart.user:
                    self.request.session["pre_log_in_cart_id"] = cart_id
                    pre_login_cart_id = cart_id

        match option:
            case ProcessCartOption.MERGE:
                if pre_login_cart_id:
                    pre_login_cart = self.get_cart_by_id(pre_login_cart_id)
                    if pre_login_cart:
                        if not self.cart:
                            self.cart = self.get_or_create_cart()

                        self.merge_carts(pre_login_cart)
                        self.request.session["pre_log_in_cart_id"] = None
            case ProcessCartOption.CLEAN:
                self.clean_cart()
                self.request.session["pre_log_in_cart_id"] = None
            case ProcessCartOption.KEEP:
                pass
            case _:
                raise InvalidProcessCartOptionException(
                    f"Invalid option: {option}"
                )

    def get_or_create_cart(self):
        user = self.request.user
        if user.is_authenticated:
            cart, created = Cart.objects.get_or_create(user=user)

            pre_login_cart_id = self.request.session.get("pre_log_in_cart_id")
            if pre_login_cart_id:
                pre_login_cart = self.get_cart_by_id(pre_login_cart_id)
                if pre_login_cart:
                    self.merge_carts(pre_login_cart)
                self.request.session["pre_log_in_cart_id"] = None

            self.request.session["cart_id"] = cart.id
            return cart
        else:
            if hasattr(self.request.session, "session_key"):
                session_key = self.request.session.session_key
                if not session_key and hasattr(self.request.session, "create"):
                    self.request.session.create()
                    session_key = self.request.session.session_key
            else:
                session_key = self.request.session.get("session_key")

            if not session_key:
                session_key = str(uuid.uuid4())
                self.request.session["session_key"] = session_key

            cart = Cart.objects.filter(session_key=session_key).first()

            if not cart:
                cart_id = self.request.session.get("cart_id")
                if cart_id:
                    cart = Cart.objects.filter(id=cart_id).first()
                    if cart and not cart.user:
                        cart.session_key = session_key
                        cart.save()

            if not cart:
                cart = Cart.objects.create(session_key=session_key)

            self.request.session["cart_id"] = cart.id
            return cart

    def merge_carts(self, pre_login_cart: Cart):
        if not self.cart or not pre_login_cart:
            return

        if self.cart.id == pre_login_cart.id:
            return

        for item in pre_login_cart.items.all():
            existing_item = CartItem.objects.filter(
                cart=self.cart, product=item.product
            ).first()

            if existing_item:
                existing_item.quantity += item.quantity
                existing_item.save()
                item.delete()
            else:
                item.cart = self.cart
                item.save()

        self.cart_items = self.cart.get_items()

        pre_login_cart.delete()

        if hasattr(self, "request") and hasattr(self.request, "session"):
            self.request.session["pre_log_in_cart_id"] = None

    def clean_cart(self):
        if self.cart:
            self.cart.items.all().delete()
            self.cart_items = []

    @staticmethod
    def get_cart_by_id(cart_id: int):
        return Cart.objects.filter(id=cart_id).first()

    def get_cart_item(self, product_id: int | None):
        if self.cart:
            return self.cart.items.filter(product_id=product_id).first()
        return None

    def create_cart_item(self, product: Product, quantity: int):
        if not self.cart:
            raise CartNotSetException("Cart is not set.")
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
