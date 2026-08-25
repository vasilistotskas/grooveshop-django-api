from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from giftcard.views import GiftCardViewSet

app_name = "giftcard"

urlpatterns = [
    path(
        "giftcard/check",
        GiftCardViewSet.as_view({"post": "check"}),
        name="giftcard-check",
    ),
    path(
        "giftcard/purchase",
        GiftCardViewSet.as_view({"post": "purchase"}),
        name="giftcard-purchase",
    ),
    path(
        "giftcard/mine",
        GiftCardViewSet.as_view({"get": "mine"}),
        name="giftcard-mine",
    ),
]

urlpatterns = format_suffix_patterns(urlpatterns)
