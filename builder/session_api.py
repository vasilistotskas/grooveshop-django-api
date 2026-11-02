"""The storefront editor's sign-in: trade a ticket for a staff token, end it.

Both routes are called by the storefront's SERVER, never by a browser
(``docs/builder.md``); the token goes into the storefront's own HttpOnly
cookie."""

from __future__ import annotations

from django_tenants.utils import get_public_schema_name, schema_context
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from builder import staff_session
from builder.serializers import StaffSerializer
from core.api.serializers import ErrorResponseSerializer
from core.api.throttling import StaffLoginThrottle
from tenant.api_tokens import PlatformStaffTokenAuthentication

TAGS = ["Builder"]


class StaffSessionTicketSerializer(serializers.Serializer):
    ticket = serializers.CharField(max_length=128)


class StaffSessionStaffSerializer(StaffSerializer):
    role = serializers.CharField()


class StaffSessionResponseSerializer(serializers.Serializer):
    token = serializers.CharField()
    expires_at = serializers.DateTimeField()
    staff = StaffSessionStaffSerializer()


class StaffSessionView(APIView):
    """``POST``: open a session from a ticket. ``DELETE``: end it."""

    authentication_classes = [PlatformStaffTokenAuthentication]

    def get_permissions(self):
        if self.request.method == "POST":
            return [AllowAny()]
        return [IsAuthenticated()]

    def get_throttles(self):
        # Fails closed while Redis cannot count (``core.api.throttling``):
        # this spends a credential.
        return [StaffLoginThrottle()] if self.request.method == "POST" else []

    throttle_scope = "builder_session"

    @extend_schema(
        tags=TAGS,
        request=StaffSessionTicketSerializer,
        responses={
            200: StaffSessionResponseSerializer,
            400: OpenApiResponse(response=ErrorResponseSerializer),
        },
        description=(
            "Spend a single-use ticket (minted by the store admin's `Edit "
            "storefront`) for a staff token that works on the builder's "
            "routes only (`Authorization: StaffBearer <token>`), is not "
            "renewed by use and ends at `expiresAt`. Any problem with the "
            "ticket, or with the person's right to edit this store's pages "
            "now, is `400 invalid_ticket`."
        ),
    )
    def post(self, request: Request) -> Response:
        body = StaffSessionTicketSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        session = staff_session.open_session(body.validated_data["ticket"])
        return Response(
            {
                "token": session.token,
                "expires_at": session.expires_at,
                "staff": {
                    "id": session.staff_id,
                    "name": session.name,
                    "role": session.role,
                },
            }
        )

    @extend_schema(tags=TAGS, request=None, responses={204: None})
    def delete(self, request: Request) -> Response:
        with schema_context(get_public_schema_name()):
            request.auth.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
