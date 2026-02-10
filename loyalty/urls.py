from django.urls import path

from loyalty.views.loyalty import LoyaltyViewSet

app_name = "loyalty"

urlpatterns = [
    path(
        "loyalty/summary",
        LoyaltyViewSet.as_view({"get": "summary"}),
        name="loyalty-summary",
    ),
    path(
        "loyalty/transactions",
        LoyaltyViewSet.as_view({"get": "transactions"}),
        name="loyalty-transactions",
    ),
    path(
        "loyalty/redeem",
        LoyaltyViewSet.as_view({"post": "redeem"}),
        name="loyalty-redeem",
    ),
    path(
        "loyalty/product/<int:pk>/points",
        LoyaltyViewSet.as_view({"get": "product_points"}),
        name="loyalty-product-points",
    ),
    path(
        "loyalty/tiers",
        LoyaltyViewSet.as_view({"get": "tiers"}),
        name="loyalty-tiers",
    ),
]
