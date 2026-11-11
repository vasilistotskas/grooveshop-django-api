"""The staff connect host: where a store's staff connect an AI app.

``settings.STAFF_CONNECT_HOST`` (``connect.grooveshop.space``) is served
on the PUBLIC schema with its own URLconf (``agent.urls_connect``),
which holds only the staff issuer's OAuth endpoints and the pages below.
It is a separate host so the platform admin's host can stay entirely
behind its basic-auth, which an AI app cannot answer.

- **Sign in** (``StaffConnectLoginView``) with the admin's own form
  (``PlatformAdminAuthenticationForm``), so the staff lockouts and a
  superuser's second factor apply, never allauth's account login.
- **Consent** (``StaffAuthorizationView``): allauth's authorize view,
  held to one store. The request names the store in its ``resource``
  (``https://<store>/mcp/admin``); the person must be on that store's
  team, and the page offers only the scopes their role opens.

Both pages wear the store being connected: its name, logo and colours,
read under the store's own tenant context.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from allauth.idp.oidc.views import AuthorizationView
from django import forms
from django.conf import settings
from django.contrib.auth import logout
from django.contrib.auth.views import LoginView
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.template import Context
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django_tenants.utils import tenant_context

from admin.forms import PlatformAdminAuthenticationForm
from admin.staff_mfa import Step
from agent.staff import (
    SCOPE_OFFLINE_ACCESS,
    STAFF_SCOPE_DISPLAY,
    STAFF_SCOPE_PERMISSIONS,
    scopes_for_role,
    store_for_staff_resource,
)
from core.templatetags.store_brand import store_brand
from tenant.auth_backends import PLATFORM_IDENTITY_ATTR
from tenant.features import feature_on
from tenant.membership import get_membership
from tenant.models import TenantMembershipRole

LOGIN_URL_NAME = "staff-connect-login"


def _brand(request: HttpRequest, store: Any | None) -> dict[str, Any] | None:
    """The store's marks, read in its own schema; None without a store."""
    if store is None:
        return None
    with tenant_context(store):
        return store_brand(Context({"request": request}))


def _store_in_next(next_url: str) -> Any | None:
    """The store an authorize URL in ``next`` connects, for the brand."""
    resources = parse_qs(urlsplit(next_url).query).get("resource", [])
    return store_for_staff_resource(resources[0]) if resources else None


class StaffConnectAuthenticationForm(PlatformAdminAuthenticationForm):
    """The admin's sign-in, without setting up an authenticator here.

    A superuser who has none yet sets it up in the admin, where the
    recovery codes are shown and kept; this page is reached from an AI
    app and is not the place to hand them out.
    """

    def _password_step(self) -> None:
        super()._password_step()
        if self.step is Step.ENROL:
            self._abandon()
            raise forms.ValidationError(
                _(
                    "Set up your authenticator app in the admin first, "
                    "then connect again."
                ),
                code="mfa_enrolment_required",
            )


class StaffConnectLoginView(LoginView):
    form_class = StaffConnectAuthenticationForm
    template_name = "agent/staff_connect/login.html"

    def get_default_redirect_url(self) -> str:
        # Reached without an AI app's authorize request: nothing to go
        # back to, so the page says where to start.
        return reverse(LOGIN_URL_NAME)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        store = _store_in_next(self.get_redirect_url())
        context["store"] = store
        context["brand"] = _brand(self.request, store)
        context["platform_name"] = settings.SITE_NAME
        return context


class StaffAuthorizationForm(forms.Form):
    """The consent: the scopes the AI app asked for that the role opens.

    allauth's own form also reads the person's verified email addresses
    (allauth.account), which have no table in the public schema and
    which no staff scope uses.
    """

    request = forms.CharField(widget=forms.HiddenInput)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.pop("user")
        requested = kwargs.pop("requested_scopes")
        super().__init__(*args, **kwargs)
        order = [*STAFF_SCOPE_PERMISSIONS, SCOPE_OFFLINE_ACCESS]
        choices = [
            (scope, STAFF_SCOPE_DISPLAY[scope])
            for scope in order
            if scope in requested
        ]
        self.fields["scopes"] = forms.MultipleChoiceField(
            choices=choices,
            label=_("Grant permissions"),
            widget=forms.CheckboxSelectMultiple,
            initial=[scope for scope, _label in choices],
            required=True,
        )

    def clean_scopes(self) -> list[str]:
        scopes = self.cleaned_data["scopes"]
        if not any(scope in STAFF_SCOPE_PERMISSIONS for scope in scopes):
            raise forms.ValidationError(
                _("Choose at least one thing the app may view."),
                code="no_staff_scope",
            )
        return scopes


class StaffConnectRefused(Exception):
    def __init__(
        self, message: Any, store: Any | None, *, status: int = 403
    ) -> None:
        super().__init__(str(message))
        self.message = message
        self.store = store
        self.status = status


def _named_store(resources: list[str]) -> Any:
    """The one store an authorize request names, or a refusal."""
    store = (
        store_for_staff_resource(resources[0]) if len(resources) == 1 else None
    )
    if store is None:
        raise StaffConnectRefused(
            _("This app did not name a store you can connect it to."),
            None,
            status=400,
        )
    return store


@dataclass(frozen=True)
class _Connection:
    store: Any
    role: str
    allowed: frozenset[str]


class StaffAuthorizationView(AuthorizationView):
    """allauth's authorize view, for a store's staff on one store."""

    form_class = StaffAuthorizationForm
    template_name = "agent/staff_connect/authorize.html"

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            # Before anything else, so a request naming no store is
            # answered here and not after a sign-in it cannot use.
            _named_store(request.GET.getlist("resource"))
            return super().get(request, *args, **kwargs)
        except StaffConnectRefused as refused:
            return self._refused(refused)

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            return super().post(request, *args, **kwargs)
        except StaffConnectRefused as refused:
            return self._refused(refused)

    def _login_required(self, request: HttpRequest) -> HttpResponse | None:
        """Send anyone not signed in as staff to this host's sign-in.

        ``prompt=login`` signs the current person out first, as it asks;
        allauth would send both to its account login, which knows neither
        the staff lockouts nor the second factor.
        """
        prompts = (request.GET.get("prompt") or "").split()
        signed_in = request.user.is_authenticated and getattr(
            request.user, PLATFORM_IDENTITY_ATTR, False
        )
        if signed_in and "login" not in prompts:
            return None
        if request.user.is_authenticated:
            logout(request)
        query = request.GET.copy()
        query.pop("prompt", None)
        next_url = f"{request.path}?{query.urlencode()}"
        return HttpResponseRedirect(
            f"{reverse(LOGIN_URL_NAME)}?{urlencode({'next': next_url})}"
        )

    def _connection(self) -> _Connection:
        cached = getattr(self, "_staff_connection", None)
        if cached is not None:
            return cached
        store = _named_store(self._request_info.get("resources") or [])
        user = self.request.user
        with tenant_context(store):
            if not feature_on("staff_ai_access"):
                raise StaffConnectRefused(
                    _(
                        "This store has not turned on AI app access for "
                        "its team."
                    ),
                    store,
                )
            membership = get_membership(user, store)
            allowed = frozenset(scopes_for_role(user)) if membership else None
        if membership is None:
            raise StaffConnectRefused(
                _("Your account is not on this store's team."), store
            )
        if not allowed:
            raise StaffConnectRefused(
                _(
                    "Your role in this store cannot view anything through an AI app."
                ),
                store,
            )
        self._staff_connection = _Connection(
            store=store, role=membership.role, allowed=allowed
        )
        return self._staff_connection

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        allowed = self._connection().allowed | {SCOPE_OFFLINE_ACCESS}
        kwargs["requested_scopes"] = [
            scope for scope in kwargs["requested_scopes"] if scope in allowed
        ]
        return kwargs

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        connection_ = self._connection()
        context.update(
            {
                "platform_name": settings.SITE_NAME,
                "store": connection_.store,
                "brand": _brand(self.request, connection_.store),
                "role_display": TenantMembershipRole(connection_.role).label,
            }
        )
        return context

    def _refused(self, refused: StaffConnectRefused) -> HttpResponse:
        return render(
            self.request,
            "agent/staff_connect/refused.html",
            {
                "platform_name": settings.SITE_NAME,
                "message": refused.message,
                "store": refused.store,
                "brand": _brand(self.request, refused.store),
            },
            status=refused.status,
        )
