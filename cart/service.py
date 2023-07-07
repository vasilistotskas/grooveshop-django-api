from __future__ import annotations

from typing import Literal

from django.http import HttpRequest

from cart.models import Cart
from cart.models import CartItem
from core import caches
from product.models.product import Product


class CartService:
    def __init__(self, request: HttpRequest):
        cart_id = request.session.get("cart_id")
        self.cart = self.get_or_create_cart(request.user, cart_id)
        request.session["cart_id"] = self.cart.id
        self.cart_items = self.cart.get_items()

    def __str__(self):
        return f"Cart {self.cart.user}"

    def __len__(self):
        return self.cart.total_items

    def __iter__(self):
        yield from self.cart_items

    def process_user_cart(
        self, request: HttpRequest, option: Literal["merge", "clean", "keep"]
    ) -> None:
        user_cart = self.get_or_create_cart(request.user)
        pre_login_cart_id = caches.get(str(request.user.id))

        if option == "merge" and pre_login_cart_id:
            pre_login_cart = self.get_cart_by_id(pre_login_cart_id)
            self.merge_carts(request, user_cart, pre_login_cart)
        elif option == "clean":
            self.clean_user_cart(user_cart)

    @staticmethod
    def get_or_create_cart(user, cart_id=None):
        if user.is_authenticated and cart_id:
            cart, _ = Cart.objects.get_or_create(user=user)
        elif user.is_authenticated:
            cart, _ = Cart.objects.get_or_create(user=user)
        elif cart_id:
            cart, _ = Cart.objects.get_or_create(id=int(cart_id))
        else:
            cart = Cart.objects.create()
        return cart

    @staticmethod
    def merge_carts(
        request: HttpRequest, user_cart: Cart, pre_login_cart: Cart
    ) -> None:
        for item in pre_login_cart.cart_item_cart.all():
            if not CartItem.objects.filter(
                cart=user_cart, product=item.product
            ).exists():
                item.cart = user_cart
                item.save()
        pre_login_cart.delete()
        caches.delete(str(request.user.id))

    @staticmethod
    def clean_user_cart(user_cart: Cart) -> None:
        user_cart.cart_item_cart.all().delete()

    def get_cart_by_id(self, cart_id):
        try:
            return Cart.objects.get(id=cart_id)
        except Cart.DoesNotExist:
            return None

    def get_cart_item(self, product_id: int) -> CartItem:
        return self.cart.cart_item_cart.get(product_id=product_id)

    def create_cart_item(self, product: Product, quantity: int) -> CartItem:
        cart_item = CartItem.objects.create(
            cart=self.cart, product=product, quantity=quantity
        )
        self.cart_items.append(cart_item)
        return cart_item

    def update_cart_item(self, product_id: int, quantity: int) -> CartItem:
        cart_item = self.cart.cart_item_cart.get(product_id=product_id)
        cart_item.quantity = quantity
        cart_item.save()
        return cart_item

    def delete_cart_item(self, product_id: int) -> None:
        self.cart.cart_item_cart.get(product_id=product_id).delete()
        self.cart_items = self.cart.get_items()
