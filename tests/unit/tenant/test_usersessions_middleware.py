"""TenantAwareUserSessionsMiddleware: schema-correct session tracking.

Platform staff are PUBLIC-schema identities; on a tenant host the stock
allauth middleware inserts a ``UserSession`` row whose user FK points at
the TENANT's user table, where the staff pk does not exist —
ForeignKeyViolation on every authenticated admin request (observed live
on staging 2026-08-19). Staff rows must be written inside the public
schema; customer sessions keep the stock per-request-schema behavior.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.contrib.auth import BACKEND_SESSION_KEY
from django.http import HttpResponse
from django.test import RequestFactory

from tenant.auth_backends import PLATFORM_STAFF_BACKEND_PATH
from tenant.middleware import TenantAwareUserSessionsMiddleware


def _request(rf: RequestFactory, *, staff_session: bool):
    request = rf.get("/admin/")
    request.session = MagicMock()
    request.session.session_key = "sess-key"
    request.session.get = lambda key, default=None: (
        PLATFORM_STAFF_BACKEND_PATH
        if (staff_session and key == BACKEND_SESSION_KEY)
        else default
    )
    request.user = MagicMock()
    request.user.is_authenticated = True
    return request


def _run(request):
    middleware = TenantAwareUserSessionsMiddleware(
        lambda _request: HttpResponse()
    )
    # The middleware imports schema_context function-locally, so the
    # patch targets the source module attribute (fetched at call time).
    with (
        patch(
            "allauth.usersessions.models.UserSession.objects"
        ) as mock_manager,
        patch("django_tenants.utils.schema_context") as mock_ctx,
    ):
        middleware(request)
    return mock_manager, mock_ctx


def test_platform_staff_session_row_written_in_public_schema(settings):
    settings.USERSESSIONS_TRACK_ACTIVITY = True
    manager, ctx = _run(_request(RequestFactory(), staff_session=True))

    manager.create_from_request.assert_called_once()
    ctx.assert_called_once()
    assert ctx.call_args.args == ("public",)


def test_customer_session_keeps_request_schema(settings):
    settings.USERSESSIONS_TRACK_ACTIVITY = True
    manager, ctx = _run(_request(RequestFactory(), staff_session=False))

    manager.create_from_request.assert_called_once()
    ctx.assert_not_called()


def test_anonymous_request_skips_tracking(settings):
    settings.USERSESSIONS_TRACK_ACTIVITY = True
    request = _request(RequestFactory(), staff_session=False)
    request.user.is_authenticated = False
    manager, ctx = _run(request)

    manager.create_from_request.assert_not_called()
    ctx.assert_not_called()
