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
        self.cart = self.get_cart()
        self.cart_items = self.cart.get_items() if self.cart else []
        if self.cart:
            self.cart.refresh_last_activity()

    def process_cart(self, option: ProcessCartOption):
        pre_login_cart_id = self.request.session.get("pre_log_in_cart_id")
        if isinstance(pre_login_cart_id, (int, str)):
            pre_login_cart_id = int(pre_login_cart_id)

        match option:
            case ProcessCartOption.MERGE:
                if pre_login_cart_id:
                    pre_login_cart = self.get_cart_by_id(pre_login_cart_id)
                    if pre_login_cart:
                        self.merge_carts(pre_login_cart)
            case ProcessCartOption.CLEAN:
                self.clean_cart()
            case ProcessCartOption.KEEP:
                pass
            case _:
                raise InvalidProcessCartOptionException(
                    f"Invalid option: {option}"
                )

    def get_cart(self):
        cart_id = self.request.session.get("cart_id")
        user = self.request.user
        if user.is_authenticated:
            cart_qs = Cart.objects.filter(user=user)
            cart = cart_qs.first()
            return cart
        elif cart_id:
            cart_qs = Cart.objects.filter(id=cart_id)
            cart = cart_qs.first()
            return cart
        return None

    def merge_carts(self, pre_login_cart: Cart):
        for item in pre_login_cart.items.all():
            if not CartItem.objects.filter(
                cart=self.cart, product=item.product
            ).exists():
                item.cart = self.cart
                item.save()
        pre_login_cart.delete()
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
