from __future__ import annotations

from enum import Enum
from enum import unique

from cart.models import Cart
from cart.models import CartItem
from core.caches import cache_instance
from product.models.product import Product


@unique
class ProcessUserCartOption(Enum):
    MERGE = "merge"
    CLEAN = "clean"
    KEEP = "keep"


class CartService:
    def __init__(self, request):
        self.user = (
            request.user
            if hasattr(request, "user") and request.user.is_authenticated
            else None
        )
        cart_id = request.session.get("cart_id")
        self.cart = self.get_or_create_cart(cart_id)
        self.cart_items = []

    def __str__(self):
        return f"Cart {self.cart.user}"

    def __len__(self):
        return self.cart.total_items

    def __iter__(self):
        yield from self.cart_items

    def process_user_cart(self, request, option: ProcessUserCartOption) -> None:
        user_cart = self.get_or_create_cart(request.user)
        pre_login_cart_id = cache_instance.get(str(request.user.id))

        if not isinstance(pre_login_cart_id, (int, str)):
            pre_login_cart_id = None

        match option:
            case ProcessUserCartOption.MERGE:
                if pre_login_cart_id:
                    pre_login_cart = self.get_cart_by_id(int(pre_login_cart_id))
                    self.merge_carts(request, user_cart, pre_login_cart)
                else:
                    pass
            case ProcessUserCartOption.CLEAN:
                self.clean_user_cart(user_cart)
            case ProcessUserCartOption.KEEP:
                pass
            case _:
                raise ValueError("Invalid option")

    def get_or_create_cart(self, cart_id=None) -> Cart:
        if self.user and cart_id:
            cart, _ = Cart.objects.get_or_create(user=self.user)
        elif self.user:
            cart, _ = Cart.objects.get_or_create(user=self.user)
        elif cart_id:
            cart, _ = Cart.objects.get_or_create(id=int(cart_id))
        else:
            cart = Cart.objects.create()
        return cart

    def merge_carts(self, request, user_cart: Cart, pre_login_cart: Cart) -> None:
        for item in pre_login_cart.cart_item_cart.all():
            if not CartItem.objects.filter(
                cart=user_cart, product=item.product
            ).exists():
                item.cart = user_cart
                item.save()
        pre_login_cart.delete()
        cache_instance.delete(str(request.user.id))

    @staticmethod
    def clean_user_cart(user_cart: Cart) -> None:
        user_cart.cart_item_cart.all().delete()

    @staticmethod
    def get_cart_by_id(cart_id: int) -> Cart | None:
        try:
            return Cart.objects.get(id=cart_id)
        except Cart.DoesNotExist:
            return None

    def get_cart_item(self, product_id: int | None) -> CartItem | None:
        try:
            cart_item = self.cart.cart_item_cart.get(product_id=product_id)
            return cart_item
        except CartItem.DoesNotExist:
            return None

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
