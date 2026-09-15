from django.urls import path

from promotion.views import (
    ProductPromotionListView,
    PublicPromotionListView,
)

app_name = "promotion"

urlpatterns = [
    path(
        "promotion",
        PublicPromotionListView.as_view(),
        name="promotion-public-list",
    ),
    path(
        "promotion/product/<int:product_id>",
        ProductPromotionListView.as_view(),
        name="promotion-product-list",
    ),
]
