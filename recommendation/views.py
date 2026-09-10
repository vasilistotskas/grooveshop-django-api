"""The two public endpoints: read suggestions, record what happened.

Both are ``AllowAny`` — explicit, per the project rule that an
anonymous endpoint declares itself — and both sit behind the tenant's
``recommendations_enabled`` plan flag, which answers 404 when off so
the endpoint is indistinguishable from a route that does not exist.

The read path writes nothing. ``impression_id`` is a correlation id
minted per response; the IMPRESSION itself is reported by the client
through the events endpoint when the strip actually becomes visible,
because "served" is not "shown" — a strip below the fold that nobody
scrolls to must not count against a strategy's click-through. That
also lets the storefront cache this body per (surface, seed) for a few
minutes without the count meaning cache fills: attribution rows carry
the viewer's session, so a shared correlation id is harmless.
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
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle

from core.api.serializers import ErrorResponseSerializer
from core.api.throttling import RecommendationEventThrottle
from recommendation.engine import SuggestionContext, suggest
from recommendation.hydrate import hydrate_pairs
from recommendation.serializers import (
    RecommendationEventRequestSerializer,
    RecommendationEventResponseSerializer,
    RecommendationQuerySerializer,
    RecommendationResponseSerializer,
)
from tenant.permissions import IsRecommendationsEnabled


def _session_key(request) -> str:
    session = getattr(request, "session", None)
    return (getattr(session, "session_key", None) or "")[:40]


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
        "impression event with impression_id once the strip is shown, "
        "and echo it on click events."
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
@permission_classes([AllowAny, IsRecommendationsEnabled])
@throttle_classes([AnonRateThrottle, UserRateThrottle])
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
        "Feeds the learning loop. Pass the impression_id from the "
        "suggestions response. Attach events are written server-side "
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
@permission_classes([AllowAny, IsRecommendationsEnabled])
@throttle_classes(
    [RecommendationEventThrottle, AnonRateThrottle, UserRateThrottle]
)
def recommendation_event(request):
    serializer = RecommendationEventRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data

    from recommendation.tasks import record_recommendation_event

    record_recommendation_event.delay(
        kind=data["kind"],
        surface=data["surface"],
        impression_id=str(data["impression_id"]),
        items=[
            {
                "product_id": item["product_id"],
                "strategy": item["strategy"],
                "position": item.get("position"),
            }
            for item in data["items"]
        ],
        seed_id=data.get("seed_id"),
        session_key=_session_key(request),
        user_id=_user_id(request),
    )
    return Response({"detail": _("Accepted.")}, status=status.HTTP_202_ACCEPTED)
