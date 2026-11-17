"""Which store features are on (staff route)."""

from __future__ import annotations

from drf_spectacular.utils import (
    extend_schema,
)
from rest_framework.request import Request
from rest_framework.response import Response

from builder import (
    features,
)
from builder.schema import TAGS
from builder.serializers import (
    BuilderFeaturesSerializer,
)
from builder.views.base import StaffView


class FeaturesView(StaffView):
    """Which store features are on, and where this person turns one on."""

    @extend_schema(
        tags=TAGS,
        responses={200: BuilderFeaturesSerializer},
        description=(
            "`{features: {<name>: {on, turnOnUrl}}}` for every store feature "
            "(the names of the tenant payload's `features`). `on`: its plan "
            "flag and every setting it needs are on. `turnOnUrl`: the store "
            "admin page of the first setting that is off, only for a role "
            "that may change settings (Admin, Owner); null when the feature "
            "is on, when the plan keeps it off, or for any other role."
        ),
    )
    def get(self, request: Request) -> Response:
        return Response({"features": features.feature_states(request)})
