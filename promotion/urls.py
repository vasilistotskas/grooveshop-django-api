from django.urls import path

from promotion.views import PublicPromotionListView

app_name = "promotion"

urlpatterns = [
    path(
        "promotion",
        PublicPromotionListView.as_view(),
        name="promotion-public-list",
    ),
]
