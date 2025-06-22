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
