"""The two public endpoints: read suggestions, record what happened.

Both are ``AllowAny`` — explicit, per the project rule that an
anonymous endpoint declares itself — and both sit behind the tenant's
``recommendations_enabled`` plan flag, which answers 404 when off so
the endpoint is indistinguishable from a route that does not exist.

The read path writes nothing. The IMPRESSION is reported by the client
through the events endpoint when the strip actually becomes visible,
because "served" is not "shown" — a strip below the fold that nobody
scrolls to must not count against a strategy's click-through.

An impression is one showing, and its id is minted where the showing is
reported: an impression event that names no id is given one here, which
the response returns, and the showing's clicks and the add to cart that
follows one carry it. Nothing upstream of that request can mint it per
viewer: the storefront caches the suggestions body per (surface, seed)
and the edge caches the page the strip is drawn into, so one body
reaches every viewer while it is cached, and so does the
``impression_id`` in it. The beacon is the first request that is one
viewer's and passes no cache, and its own session, cart and customer are
what the impression is recorded with, so an ``attach`` matched through
the id is the viewer's own. The id is a random UUID Django minted, so an
id a client made up matches no impression it could attach to.
"""

from __future__ import annotations

import uuid

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    permission_classes,
    throttle_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from core.api.serializers import ErrorResponseSerializer
from core.api.throttling import (
    AnonIpRateThrottle,
    RecommendationEventThrottle,
    UserBudgetThrottle,
)
from core.cart_id import cart_uuid_from_request
from core.sessions import persisted_session_key
from recommendation.engine import SuggestionContext, suggest
from recommendation.hydrate import hydrate_pairs
from recommendation.serializers import (
    RecommendationEventRequestSerializer,
    RecommendationEventResponseSerializer,
    RecommendationQuerySerializer,
    RecommendationResponseSerializer,
)
from tenant.celery import publish_or_drop
from tenant.permissions import (
    IsCatalogueEnabled,
    IsRecommendationsEnabled,
    IsRecommendationsRuntimeEnabled,
)


def _user_id(request) -> int | None:
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return user.pk
    return None


@extend_schema(  # ty: ignore[invalid-argument-type]
    summary=_("Suggested products for a surface"),
    description=_(
        "Products to show beside the seed product(s) on a given surface "
        "(product page, cart, out-of-stock, empty cart). Each item "
        "carries the strategy that produced it and, for merchant-curated "
        "links, the relation type. An empty list means the store has "
        "nothing worth showing here — render nothing. Report an "
        "impression event once the strip is shown, with no impression_id: "
        "its response names the showing's id, which the strip's click "
        "events carry."
    ),
    tags=["Recommendations"],
    parameters=[RecommendationQuerySerializer],
    responses={
        200: RecommendationResponseSerializer,
        400: ErrorResponseSerializer,
        404: ErrorResponseSerializer,
    },
)
@api_view(["GET"])
@permission_classes(
    [
        AllowAny,
        IsCatalogueEnabled,
        IsRecommendationsEnabled,
        IsRecommendationsRuntimeEnabled,
    ]
)
@throttle_classes([AnonIpRateThrottle, UserBudgetThrottle])
def recommendations(request):
    query = RecommendationQuerySerializer(data=request.query_params)
    query.is_valid(raise_exception=True)
    data = query.validated_data

    suggestions = suggest(
        SuggestionContext(
            surface=data["surface"],
            seed_ids=tuple(data["seed_ids"]),
            exclude_ids=tuple(data["exclude_ids"]),
            limit=data.get("limit"),
        )
    )
    pairs = hydrate_pairs(suggestions, context={"request": request})
    impression_id = uuid.uuid4()

    items = [
        {
            "product": product,
            "reason": {
                "strategy": suggestion.strategy,
                "relation_type": suggestion.relation_type,
                "score": suggestion.score,
            },
        }
        for suggestion, product in pairs
    ]

    return Response(
        {
            "surface": data["surface"],
            "items": items,
            "impression_id": impression_id,
        },
        status=status.HTTP_200_OK,
    )


@extend_schema(  # ty: ignore[invalid-argument-type]
    summary=_("Record a suggestion impression or click"),
    description=_(
        "Feeds the learning loop. Leave impression_id out of an "
        "impression: the response names the id minted for that showing, "
        "and its clicks carry it. Attach events are written server-side "
        "when an order completes and cannot be posted here."
    ),
    tags=["Recommendations"],
    request=RecommendationEventRequestSerializer,
    responses={
        202: RecommendationEventResponseSerializer,
        400: ErrorResponseSerializer,
        404: ErrorResponseSerializer,
    },
)
@api_view(["POST"])
@permission_classes(
    [AllowAny, IsRecommendationsEnabled, IsRecommendationsRuntimeEnabled]
)
@throttle_classes(
    [RecommendationEventThrottle, AnonIpRateThrottle, UserBudgetThrottle]
)
def recommendation_event(request):
    serializer = RecommendationEventRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data

    from recommendation.tasks import record_recommendation_event

    # The cart is the identity an ``attach`` is later matched on: the
    # storefront proxy forwards the same X-Cart-Id the basket uses.
    cart_uuid = cart_uuid_from_request(request)
    impression_id = data.get("impression_id") or uuid.uuid4()
    publish_or_drop(
        record_recommendation_event,
        kind=data["kind"],
        surface=data["surface"],
        impression_id=str(impression_id),
        items=[
            {
                "product_id": item["product_id"],
                "strategy": item["strategy"],
                "position": item.get("position"),
            }
            for item in data["items"]
        ],
        seed_id=data.get("seed_id"),
        session_key=persisted_session_key(request),
        user_id=_user_id(request),
        cart_uuid=str(cart_uuid) if cart_uuid else None,
    )
    return Response(
        {"detail": _("Accepted."), "impression_id": impression_id},
        status=status.HTTP_202_ACCEPTED,
    )
