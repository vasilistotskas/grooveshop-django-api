"""The RFC 8058 one-click unsubscribe target.

Marketing mail names this URL in ``List-Unsubscribe`` and flags it with
``List-Unsubscribe-Post``. A mailbox provider POSTs to it, with no cookies
and no credentials, when the recipient presses its unsubscribe button.

There is deliberately no ``GET``: link scanners and previewers follow every
URL in a mail, so a GET that opted anyone out would unsubscribe people who
never asked. The storefront page that confirms by button (and POSTs here)
ships separately; until then a GET answers 405.
"""

from __future__ import annotations

import logging

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api.serializers import ErrorResponseSerializer
from core.api.throttling import EmailUnsubscribeThrottle
from emails import optout
from emails.models import EmailOptOut

logger = logging.getLogger(__name__)

INVALID_TOKEN_CODE = "unsubscribe_token_invalid"


class EmailUnsubscribeView(APIView):
    """``POST /api/v1/emails/unsubscribe/<token>``."""

    permission_classes = [AllowAny]
    # No authentication means no session, so nothing for CSRF to protect:
    # a mailbox provider's POST carries no cookie or token (RFC 8058).
    authentication_classes = []
    throttle_classes = [EmailUnsubscribeThrottle]

    @extend_schema(
        operation_id="unsubscribeFromEmailOneClick",
        summary=_("RFC 8058 one-click unsubscribe from marketing email"),
        description=_(
            "Invoked by mail clients honoring List-Unsubscribe-Post="
            "One-Click. The token names the email type, or every marketing "
            "type, and is valid for the store that sent it only. "
            "Idempotent: repeating it changes nothing."
        ),
        tags=["Emails"],
        request=None,
        responses={200: None, 400: ErrorResponseSerializer},
    )
    def post(self, request, token: str):
        try:
            optout.opt_out_by_token(token, source=EmailOptOut.Source.ONE_CLICK)
        except optout.InvalidToken as error:
            # The reason stays in the log; the caller learns only that the
            # link is not valid, never whether it was forged, expired or
            # minted for another store.
            logger.warning("email unsubscribe refused: %s", error)
            return Response(
                {
                    "detail": str(_("Invalid unsubscribe link")),
                    "code": INVALID_TOKEN_CODE,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(status=status.HTTP_200_OK)
