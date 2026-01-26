from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from cart.views.cart import CartViewSet
from cart.views.item import CartItemViewSet

urlpatterns = [
    path(
        "cart",
        CartViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="cart-detail",
    ),
    path(
        "cart/list",
        CartViewSet.as_view({"get": "list"}),
        name="cart-list",
    ),
    path(
        "cart/reserve-stock",
        CartViewSet.as_view({"post": "reserve_stock"}),
        name="cart-reserve-stock",
    ),
    path(
        "cart/release-reservations",
        CartViewSet.as_view({"post": "release_reservations"}),
        name="cart-release-reservations",
    ),
    path(
        "cart/create-payment-intent",
        CartViewSet.as_view({"post": "create_payment_intent"}),
        name="cart-create-payment-intent",
    ),
    path(
        "cart/item",
        CartItemViewSet.as_view({"get": "list", "post": "create"}),
        name="cart-item-list",
    ),
    path(
        "cart/item/<int:pk>",
        CartItemViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="cart-item-detail",
    ),
]

urlpatterns = format_suffix_patterns(urlpatterns)
