"""The RFC 8058 one-click unsubscribe target.

Marketing mail names this URL in ``List-Unsubscribe`` and flags it with
``List-Unsubscribe-Post``. A mailbox provider POSTs to it, with no cookies
and no credentials, when the recipient presses its unsubscribe button.

A ``GET`` never opts anyone out: link scanners and previewers follow every
URL in a mail, and some mail clients open the ``List-Unsubscribe`` URL itself
with a GET. It answers 302 to the storefront page that confirms by button and
POSTs here, in the store's language (a GET carries no language of the
recipient's). The token is not read: the page learns an invalid one from the
POST's 400.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from django.http import HttpResponseRedirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api.serializers import ErrorResponseSerializer
from core.api.throttling import EmailUnsubscribeThrottle
from core.utils.i18n import store_language
from core.utils.tenant_urls import get_tenant_base_url
from emails import optout
from emails.models import EmailOptOut
from user.services.subscription import unsubscribe_from_all_marketing

logger = logging.getLogger(__name__)

INVALID_TOKEN_CODE = "unsubscribe_token_invalid"


def _invalid_token_response(error: optout.InvalidToken) -> Response:
    # The reason stays in the log; the caller learns only that the link is
    # not valid, never whether it was forged, expired or minted for another
    # store.
    logger.warning("email unsubscribe refused: %s", error)
    return Response(
        {
            "detail": str(_("Invalid unsubscribe link")),
            "code": INVALID_TOKEN_CODE,
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


class EmailUnsubscribeView(APIView):
    """``POST /api/v1/emails/unsubscribe/<token>``."""

    permission_classes = [AllowAny]
    # No authentication means no session, so nothing for CSRF to protect:
    # a mailbox provider's POST carries no cookie or token (RFC 8058).
    authentication_classes = []
    throttle_classes = [EmailUnsubscribeThrottle]

    @extend_schema(
        operation_id="confirmUnsubscribeFromEmail",
        summary=_("Open the unsubscribe confirmation page"),
        description=_(
            "Changes nothing: redirects to the storefront page where the "
            "recipient confirms the unsubscribe with a button."
        ),
        tags=["Emails"],
        parameters=[
            OpenApiParameter(
                name="Location",
                type=OpenApiTypes.URI,
                location=OpenApiParameter.HEADER,
                description=_("The storefront unsubscribe confirmation page."),
                response=[302],
            )
        ],
        responses={302: None},
    )
    def get(self, request, token: str):
        url = optout.confirm_url_for_token(token, language=store_language())
        # The token comes from the request: the redirect must still land on
        # this store's own storefront and nowhere else.
        storefront = urlsplit(get_tenant_base_url())
        if not url_has_allowed_host_and_scheme(
            url,
            allowed_hosts={storefront.netloc},
            require_https=storefront.scheme == "https",
        ):
            return _invalid_token_response(
                optout.InvalidToken("The confirm URL leaves the storefront.")
            )
        return HttpResponseRedirect(url)

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
            return _invalid_token_response(error)
        return Response(status=status.HTTP_200_OK)


class EmailUnsubscribeAllView(APIView):
    """``POST /api/v1/emails/unsubscribe/<token>/all``: the storefront
    page's second button, "stop all marketing emails from the store".

    Any valid token of this store will do, whatever type it names. Only the
    storefront page calls it (a mailbox provider's one-click POST goes to
    the type-scoped URL of the header), hence the source ``link``.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [EmailUnsubscribeThrottle]

    @extend_schema(
        operation_id="unsubscribeFromAllMarketingEmail",
        summary=_("Unsubscribe from all marketing email of the store"),
        description=_(
            "Called by the storefront confirmation page. The token names "
            "any email type of the store; every marketing type is opted "
            "out and, for an account, its active subscriptions end. "
            "Idempotent: repeating it changes nothing."
        ),
        tags=["Emails"],
        request=None,
        responses={200: None, 400: ErrorResponseSerializer},
    )
    def post(self, request, token: str):
        try:
            claims = optout.read_token(token)
        except optout.InvalidToken as error:
            return _invalid_token_response(error)
        unsubscribe_from_all_marketing(claims, source=EmailOptOut.Source.LINK)
        return Response(status=status.HTTP_200_OK)
