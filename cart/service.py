from __future__ import annotations

from cart.models import Cart
from cart.models import CartItem
from core import caches


class CartService:
    def __init__(self, request):
        self.user = request.user if request.user.is_authenticated else None
        cart_id = request.session.get("cart_id")
        self.cart = self.get_or_create_cart(cart_id)
        self.cart_items = []

    def __str__(self):
        return f"Cart {self.cart.user}"

    def __len__(self):
        return self.cart.total_items

    def __iter__(self):
        yield from self.cart_items

    def process_user_cart(self, request, option):
        user_cart = self.get_or_create_cart(request.user)
        pre_login_cart_id = caches.get(str(request.user.id))

        if option == "merge" and pre_login_cart_id:
            pre_login_cart = self.get_cart_by_id(pre_login_cart_id)
            self.merge_carts(request, user_cart, pre_login_cart)
        elif option == "clean":
            self.clean_user_cart(user_cart)
        else:
            raise ValueError("Invalid option")

    def get_or_create_cart(self, cart_id=None):
        if self.user and cart_id:
            cart, _ = Cart.objects.get_or_create(user=self.user)
        elif self.user:
            cart, _ = Cart.objects.get_or_create(user=self.user)
        elif cart_id:
            cart, _ = Cart.objects.get_or_create(id=int(cart_id))
        else:
            cart = Cart.objects.create()
        return cart

    @staticmethod
    def merge_carts(request, user_cart, pre_login_cart):
        for item in pre_login_cart.cart_item_cart.all():
            if not CartItem.objects.filter(
                cart=user_cart, product=item.product
            ).exists():
                item.cart = user_cart
                item.save()
        pre_login_cart.delete()
        caches.delete(str(request.user.id))

    @staticmethod
    def clean_user_cart(user_cart):
        user_cart.cart_item_cart.all().delete()

    @staticmethod
    def get_cart_by_id(cart_id):
        try:
            return Cart.objects.get(id=cart_id)
        except Cart.DoesNotExist:
            return None

    def get_cart_item(self, product_id):
        try:
            cart_item = self.cart.cart_item_cart.get(product_id=product_id)
            return cart_item
        except CartItem.DoesNotExist:
            return None

    def create_cart_item(self, product, quantity):
        cart_item = CartItem.objects.create(
            cart=self.cart, product=product, quantity=quantity
        )
        self.cart_items.append(cart_item)
        return cart_item

    def update_cart_item(self, product_id, quantity):
        cart_item = self.cart.cart_item_cart.get(product_id=product_id)
        cart_item.quantity = quantity
        cart_item.save()
        return cart_item

    def delete_cart_item(self, product_id):
        self.cart.cart_item_cart.get(product_id=product_id).delete()
        self.cart_items = self.cart.get_items()
