"""URLconf of the staff connect host (``settings.STAFF_CONNECT_HOST``).

Served on the PUBLIC schema instead of ``tenant.urls_public``
(``tenant.middleware.PlatformTenantMainMiddleware``), and holding nothing
but the staff issuer: its OAuth endpoints and the sign-in and consent
pages (``agent/staff_connect.py``). The platform admin, its API and
allauth's account pages are not routes here at all, so leaving this host
outside the platform's basic-auth opens nothing else.
"""

from __future__ import annotations

from allauth.idp.oidc import views as oidc_views
from django.contrib.auth.views import LogoutView
from django.http import Http404
from django.urls import include, path, reverse_lazy

from agent.staff_connect import (
    LOGIN_URL_NAME,
    StaffAuthorizationView,
    StaffConnectLoginView,
)


def _not_offered(request, *args, **kwargs):
    """The device flow is not offered by the staff issuer: its pages sign
    people in through allauth's account login (``agent/staff.py``)."""
    raise Http404


urlpatterns = [
    # Ahead of allauth's include, so these answer the same paths.
    path(
        "identity/o/authorize",
        StaffAuthorizationView.as_view(),
        name="staff-connect-authorize",
    ),
    path("identity/o/device", _not_offered),
    path("identity/o/api/device/code", _not_offered),
    path(
        "identity/staff/login/",
        StaffConnectLoginView.as_view(),
        name=LOGIN_URL_NAME,
    ),
    path(
        "identity/staff/logout/",
        LogoutView.as_view(next_page=reverse_lazy(LOGIN_URL_NAME)),
        name="staff-connect-logout",
    ),
    path("", include("allauth.idp.urls")),
    # RFC 8414: plain OAuth clients look here; the same document as the
    # OIDC discovery, as on a store's issuer (core/urls.py).
    path(
        ".well-known/oauth-authorization-server",
        oidc_views.configuration,
        name="oauth_authorization_server_metadata",
    ),
]
