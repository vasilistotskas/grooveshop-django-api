"""Public promotion read surfaces.

Automatic promotions are invisible until the cart applies them, so a
shopper never learns that spending €80 earns a gift until they have
already built an €80 cart. These endpoints are the discovery half:

* ``PublicPromotionListView`` — the storefront's ``/offers`` page: what
  is running, store-wide.
* ``ProductPromotionListView`` — the same set narrowed to one product,
  with the reason each offer is relevant to it, for the product page's
  offer panel.

Both are gated on BOTH promotion tiers — the plan flag
(``IsPromotionsEnabled``) and the merchant's runtime setting
(``IsPromotionsRuntimeEnabled``) — and both raise 404 rather than 403,
so a store with promotions off is indistinguishable from one that never
had the route. That matters more here than on the cart's coupon
endpoint, because these pages are crawlable.

"Which promotions may a shopper be told about" lives in ONE place —
``PromotionQuerySet.publicly_listable`` — shared with the checkout
coupon picker.
"""

from __future__ import annotations

from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api.serializers import ErrorResponseSerializer
from product.models.product import Product
from promotion.managers.promotion import publishable_code_q
from promotion.models import Promotion, PromotionCode
from promotion.serializers import (
    ProductPromotionSerializer,
    PublicPromotionSerializer,
)
from promotion.services import ProductPromotionService
from tenant.permissions import IsPromotionsEnabled, IsPromotionsRuntimeEnabled

# Guards the payload size on a store running a large campaign set. Far
# above any realistic number of simultaneous public offers, so it is a
# backstop rather than pagination — a page that silently truncated its
# offer list would be worse than one that shows all of them.
MAX_OFFERS = 60

# The product panel is a sidebar, not a listing: past a handful of rows
# it stops helping the purchase decision and starts burying the add-to-
# cart button. ``ProductPromotionService`` sorts most-specific-first, so
# the rows that survive the slice are the ones about THIS product.
MAX_PRODUCT_OFFERS = 8


class PublicPromotionListView(APIView):
    """Live promotions a shopper can act on, in engine-apply order."""

    permission_classes = [
        AllowAny,
        IsPromotionsEnabled,
        IsPromotionsRuntimeEnabled,
    ]

    @extend_schema(
        operation_id="listPublicPromotions",
        summary=_("List public offers"),
        description=_(
            "Currently-live promotions that a shopper can act on: every "
            "AUTOMATIC promotion, plus CODE promotions that have at "
            "least one publicly advertisable code. Personal coupons, "
            "single-use codes, and promotions that have reached their "
            "total usage limit are excluded. Returns 404 when the store "
            "has promotions disabled at either tier."
        ),
        tags=["Promotions"],
        responses={
            200: PublicPromotionSerializer(many=True),
            404: ErrorResponseSerializer,
        },
    )
    def get(self, request):
        serializer = PublicPromotionSerializer(self._offers(), many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @staticmethod
    def _offers() -> list[Promotion]:
        queryset = (
            Promotion.objects.live()
            .publicly_listable()
            .for_list()
            .prefetch_related(
                _publishable_codes_prefetch(),
                "products__translations",
                "get_products__translations",
                "categories__translations",
            )
            # Same order the engine applies them in, so the page reads
            # top-to-bottom the way a cart accumulates them.
            .order_by("priority", "id")
        )
        return list(queryset[:MAX_OFFERS])


class ProductPromotionListView(APIView):
    """Live offers that apply to one product, most specific first."""

    permission_classes = [
        AllowAny,
        IsPromotionsEnabled,
        IsPromotionsRuntimeEnabled,
    ]

    @extend_schema(
        operation_id="listProductPromotions",
        summary=_("List the offers that apply to a product"),
        description=_(
            "Currently-live, publicly advertisable promotions that "
            "touch this product, each carrying the RELATION that makes "
            "it relevant: the promotion names the product (PRODUCT), "
            "gives it away (REWARD), targets its category (CATEGORY), "
            "or applies to any order (ORDER). Scope and exclusions are "
            "resolved with the same rules the cart engine uses, so an "
            "excluded product never advertises the offer that skips "
            "it. Returns 404 when the store has promotions disabled at "
            "either tier, or when the product does not exist."
        ),
        tags=["Promotions"],
        # No explicit path parameter: spectacular derives ``productId``
        # from the ``<int:product_id>`` converter, and declaring a
        # second one by hand put BOTH in the generated client's path
        # schema.
        responses={
            200: ProductPromotionSerializer(many=True),
            404: ErrorResponseSerializer,
        },
    )
    def get(self, request, product_id: int):
        product = get_object_or_404(
            Product.objects.filter(active=True), pk=product_id
        )
        offers = ProductPromotionService.for_product(product)[
            :MAX_PRODUCT_OFFERS
        ]
        # The relation is a property of the (promotion, product) pair,
        # not of the row, so it rides on the instance for the
        # serializer to read — the same trick the engine uses for
        # expanded category ids.
        for offer in offers:
            offer.promotion._product_relation = offer.relation
        serializer = ProductPromotionSerializer(
            [offer.promotion for offer in offers], many=True
        )
        return Response(serializer.data, status=status.HTTP_200_OK)


def _publishable_codes_prefetch() -> Prefetch:
    """The codes ``PublicPromotionSerializer.get_code`` may publish."""
    return Prefetch(
        "codes",
        queryset=PromotionCode.objects.filter(publishable_code_q()).order_by(
            "created_at"
        ),
        to_attr="publishable_codes",
    )
