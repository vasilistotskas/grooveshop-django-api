"""One-time ticket minting for WebSocket authentication.

The client fetches a short-lived ticket via authenticated HTTP, then
passes it as `?ticket=<value>` in the WebSocket URL. The consumer
middleware consumes the ticket once (single-use) to authenticate the
connection.

Why tickets instead of passing the Knox token directly as a URL param:
- Proxies, CDNs, and browser history log URL query strings in plaintext.
- Knox tokens live for 7 days — logged tokens are long-lived credentials.
- Tickets expire in 60s and delete on first use, so intercepted values
  lose usefulness almost immediately.
"""

from __future__ import annotations

import logging
import secrets

from django.core.cache import cache
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

logger = logging.getLogger(__name__)

WS_TICKET_CACHE_PREFIX = "ws:ticket:"
WS_TICKET_TTL_SECONDS = 60


def build_ticket_cache_key(ticket: str) -> str:
    return f"{WS_TICKET_CACHE_PREFIX}{ticket}"


@extend_schema(
    operation_id="createWebSocketTicket",
    summary="Mint a short-lived WebSocket ticket",
    description=(
        "Returns a single-use ticket valid for 60 seconds. Pass as "
        "`?ticket=<value>` on the WebSocket connection URL."
    ),
    tags=["WebSocket"],
    request=None,
    responses={
        200: inline_serializer(
            name="WebSocketTicketResponse",
            fields={
                "ticket": serializers.CharField(),
                "expires_in": serializers.IntegerField(),
            },
        )
    },
    methods=["POST"],
)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_websocket_ticket(request):
    from knox.models import get_token_model  # noqa: PLC0415

    # Guard against the edge case where Knox tokens were revoked between
    # the moment the HTTP request was authenticated (DRF Knox auth) and
    # this line — e.g. a concurrent password-change request arrived first.
    # In practice this window is tiny, but the check is cheap.
    if not get_token_model().objects.filter(user=request.user).exists():
        logger.warning(
            "WS ticket denied: no live Knox tokens for user %s",
            request.user.pk,
        )
        return Response(
            {"detail": "No active session."},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    ticket = secrets.token_urlsafe(32)
    cache.set(
        build_ticket_cache_key(ticket),
        request.user.pk,
        WS_TICKET_TTL_SECONDS,
    )
    return Response(
        {"ticket": ticket, "expires_in": WS_TICKET_TTL_SECONDS},
        status=status.HTTP_200_OK,
    )
