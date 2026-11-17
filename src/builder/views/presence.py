"""Who else has a document open (staff route)."""

from __future__ import annotations

from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
)
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from builder import (
    services,
)
from builder.schema import TAGS
from builder.serializers import (
    BuilderDocumentInvalidErrorSerializer,
    PresenceSerializer,
    PresenceWriteSerializer,
)
from builder.views.base import StaffView
from core.api.permissions import IsStoreStaff


class PresenceView(StaffView):
    """Who else has a document open: a heartbeat, and sign-out."""

    # Nothing is stored in the database; the service asks for VIEW.
    permission_classes = [IsStoreStaff]

    @extend_schema(
        tags=TAGS,
        request=PresenceWriteSerializer,
        responses={
            200: PresenceSerializer,
            400: OpenApiResponse(BuilderDocumentInvalidErrorSerializer),
        },
        description=(
            "A heartbeat: the documents this editor has open (the full "
            "list; ones left out are let go of). Answers who ELSE has each "
            "of them open, `{documents: {key: [{id, name, since}]}}`, the "
            "caller excluded. A person lapses `BUILDER_PRESENCE_TTL_"
            "SECONDS` after their last heartbeat. It is a hint: the "
            "draft's `If-Match` revision still decides a save."
        ),
    )
    def post(self, request: Request) -> Response:
        body = PresenceWriteSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return Response(
            {
                "documents": services.heartbeat(
                    request.user, documents=body.validated_data["documents"]
                )
            }
        )

    @extend_schema(tags=TAGS, request=None, responses={204: None})
    def delete(self, request: Request) -> Response:
        services.leave_presence(request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
