from __future__ import annotations

from enum import Enum
from enum import unique

from django.http import HttpRequest
from rest_framework.request import Request

from cart.models import Cart
from cart.models import CartItem
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
    def __init__(self, cart_id: int | None = None, request: Request | HttpRequest | None = None):
        self.cart_id = cart_id
        self.cart: Cart | None = None
        self.cart_items: list[CartItem] = []

        if not cart_id and not request:
            raise CartServiceInitException()

        if request:
            self.get_or_create_cart(request)
        elif cart_id:
            self.cart = self.get_cart_by_id(cart_id)
            self.cart_items = self.cart.get_items()

    def __str__(self):
        return f"Cart {self.cart.user}"

    def __len__(self):
        return self.cart.total_items

    def __iter__(self):
        yield from self.cart_items

    def process_cart(self, request: Request | HttpRequest | None, option: ProcessCartOption) -> None:
        cart = self.get_or_create_cart(request)
        pre_login_cart_id = request.session.get("pre_log_in_cart_id")
        if isinstance(pre_login_cart_id, (int, str)):
            pre_login_cart_id = int(pre_login_cart_id)

        match option:
            case ProcessCartOption.MERGE:
                if pre_login_cart_id:
                    pre_login_cart = self.get_cart_by_id(pre_login_cart_id)
                    self.merge_carts(request, cart, pre_login_cart)
            case ProcessCartOption.CLEAN:
                self.clean_cart(cart)
            case ProcessCartOption.KEEP:
                pass
            case _:
                raise InvalidProcessCartOptionException()

    def get_or_create_cart(self, request: Request | HttpRequest | None) -> Cart:
        cart_id = request.session.get("cart_id")
        user = request.user

        if self.cart:
            return self.cart

        if user.is_authenticated:
            cart, _ = Cart.objects.get_or_create(user=user)
        elif cart_id:
            cart, _ = Cart.objects.get_or_create(id=int(cart_id))
            request.session["pre_log_in_cart_id"] = cart.id
        else:
            cart = Cart.objects.create()
            request.session["pre_log_in_cart_id"] = cart.id

        request.session["cart_id"] = cart.id
        request.session.save()
        self.cart = cart
        self.cart_items = cart.get_items()
        self.cart.refresh_last_activity()
        return cart

    @staticmethod
    def merge_carts(request: Request | HttpRequest | None, cart: Cart, pre_login_cart: Cart) -> None:
        for item in pre_login_cart.items.all():
            if not CartItem.objects.filter(cart=cart, product=item.product).exists():
                item.cart = cart
                item.save()
        pre_login_cart.delete()
        request.session["pre_log_in_cart_id"] = None

    @staticmethod
    def clean_cart(cart: Cart) -> None:
        cart.items.all().delete()

    @staticmethod
    def get_cart_by_id(cart_id: int) -> Cart | None:
        return Cart.objects.filter(id=cart_id).first()

    def get_cart_item(self, product_id: int | None) -> CartItem | None:
        return self.cart.items.filter(product_id=product_id).first()

    def create_cart_item(self, product: Product, quantity: int) -> CartItem:
        if not self.cart:
            raise CartNotSetException()

        cart_item = CartItem.objects.create(cart=self.cart, product=product, quantity=quantity)
        self.cart_items = self.cart.get_items()
        return cart_item

    def update_cart_item(self, product_id: int, quantity: int) -> CartItem:
        if not self.cart:
            raise CartNotSetException()

        cart_item = self.cart.items.get(product_id=product_id)
        cart_item.quantity = quantity
        cart_item.save()
        self.cart_items = self.cart.get_items()
        return cart_item

    def delete_cart_item(self, product_id: int) -> None:
        if not self.cart:
            raise CartNotSetException()

        self.cart.items.filter(product_id=product_id).delete()
        self.cart_items = self.cart.get_items()
