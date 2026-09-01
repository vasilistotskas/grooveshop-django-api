"""Public offers listing.

Automatic promotions are invisible until the cart applies them, so a
shopper never learns that spending €80 earns a gift until they have
already built an €80 cart. This endpoint is the discovery half: the
storefront's ``/offers`` page reads it, and it is the only public read
surface the ``promotion`` app has.

Gated on BOTH promotion tiers — the plan flag
(``IsPromotionsEnabled``) and the merchant's runtime setting
(``IsPromotionsRuntimeEnabled``) — and both raise 404 rather than 403,
so a store with promotions off is indistinguishable from one that never
had the route. That matters more here than on the cart's coupon
endpoint, because this page is crawlable.
"""

from __future__ import annotations

from django.db.models import Count, F, Prefetch, Q
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api.serializers import ErrorResponseSerializer
from promotion.enum import PromotionTrigger
from promotion.models import Promotion, PromotionCode
from promotion.serializers import PublicPromotionSerializer
from tenant.permissions import IsPromotionsEnabled, IsPromotionsRuntimeEnabled

# Guards the payload size on a store running a large campaign set. Far
# above any realistic number of simultaneous public offers, so it is a
# backstop rather than pagination — a page that silently truncated its
# offer list would be worse than one that shows all of them.
MAX_OFFERS = 60


def publishable_code_q(prefix: str = "") -> Q:
    """Codes a shopper may be shown.

    Excludes personal coupons (assigned to a user or an email) and
    single-use codes — advertising a ``usage_limit=1`` code to every
    visitor tells all but one of them about an offer they cannot use.

    ``prefix`` exists because the same condition is needed in two
    places with different anchors: a ``Count(filter=...)`` on
    ``Promotion`` must name the relation (``codes__is_active``), while a
    queryset on ``PromotionCode`` must not. Deriving both from one
    function keeps them from drifting apart.

    ``usage_limit`` is compared as ``IS NULL OR > 1`` rather than
    ``~Q(usage_limit=1)`` on purpose: a negated equality is not
    NULL-safe in SQL, and unlimited codes (the common case) carry NULL,
    so the tidier-looking form would exclude exactly the codes most
    worth advertising.
    """
    field = f"{prefix}__" if prefix else ""
    return Q(
        **{
            f"{field}is_active": True,
            f"{field}assigned_to__isnull": True,
            f"{field}assigned_to_email": "",
        }
    ) & (
        Q(**{f"{field}usage_limit__isnull": True})
        | Q(**{f"{field}usage_limit__gt": 1})
    )


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
            .for_list()
            .annotate(
                # distinct=True on both: two Counts over different
                # multi-valued relations fan the join out, and without
                # it each count would be multiplied by the other's row
                # count.
                publishable_code_count=Count(
                    "codes",
                    filter=publishable_code_q("codes"),
                    distinct=True,
                ),
                # ``usage_limit_total`` caps the ORDERS that used the
                # promotion, which is what PromotionRedemption counts —
                # matching PromotionEngine's own check so the page never
                # advertises an offer the cart would refuse with
                # USAGE_LIMIT_REACHED.
                redemption_count=Count("redemptions", distinct=True),
            )
            .filter(
                # AUTOMATIC needs no code; a CODE promotion is useless
                # to a shopper without one they are allowed to see.
                Q(trigger=PromotionTrigger.AUTOMATIC)
                | Q(publishable_code_count__gt=0)
            )
            .filter(
                # Written as an inclusive filter rather than exclude():
                # exclude() on a nullable annotation comparison drops
                # the unlimited rows too.
                Q(usage_limit_total__isnull=True)
                | Q(redemption_count__lt=F("usage_limit_total"))
            )
            .prefetch_related(
                Prefetch(
                    "codes",
                    queryset=PromotionCode.objects.filter(
                        publishable_code_q()
                    ).order_by("created_at"),
                    to_attr="publishable_codes",
                ),
                "products__translations",
                "get_products__translations",
                "categories__translations",
            )
            # Same order the engine applies them in, so the page reads
            # top-to-bottom the way a cart accumulates them.
            .order_by("priority", "id")
        )
        return list(queryset[:MAX_OFFERS])
