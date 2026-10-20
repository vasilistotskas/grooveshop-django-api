"""The admin's "forgot password" flow, for platform staff.

Staff sign in to every admin through ``PlatformAdminAuthenticationForm``,
which checks the password of a PUBLIC-schema ``UserAccount``
(``PlatformStaffBackend``). allauth's reset flow belongs to the
shopper's login and is not involved: the admin login never reads an
allauth session, and a staff member's password is a Django password on
that row. So the flow is Django's own ``PasswordResetView`` /
``PasswordResetConfirmView``, mounted on both admin sites
(``StaffPasswordResetMixin``), with four differences:

- **The public schema.** The page runs under the schema the host
  resolved: on a store host that is the store, whose ``UserAccount`` rows
  are SHOPPERS. Every lookup and the save of the new password therefore
  run in public (``PlatformPasswordChangeForm`` does the same).
- **Who may ask.** Only a staff identity that can sign in on this host: a
  superuser, or a staff member with a staff role in this store. The
  platform host serves superusers alone.
- **No enumeration.** The form answers the same page whether or not the
  address matches. The request never looks the address up: it queues one
  task per accepted request, and the task finds the account, so a match
  is not slower either. Budgets per client address and per target
  address (``settings.STAFF_PASSWORD_RESET``) fail closed when the cache
  is down, because the form makes us mail an address the caller chose.
- **MFA is not bypassed.** The reset sets a password and nothing else: it
  does not log the user in (``post_reset_login`` stays off) and leaves
  their authenticators alone, so a platform superuser still passes the
  code step of the admin login (``admin.staff_mfa``) next time. A reset
  also ends the staff API tokens the old password could have minted.

The link points at the host the request came in on, which the tenant
middleware has already matched to one of the store's own domains.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from django.conf import settings
from django.contrib.auth.forms import PasswordResetForm, SetPasswordForm
from django.contrib.auth.views import (
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.core.cache import cache
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _
from django_tenants.utils import get_public_schema_name, schema_context
from unfold.widgets import (
    UnfoldAdminEmailInputWidget,
    UnfoldAdminPasswordWidget,
)

from core.client_ip import client_ip_or_peer
from tenant.celery import dispatch_on_commit
from tenant.membership import get_current_tenant, get_membership

logger = logging.getLogger(__name__)

#: Keys under ``strict:`` are never served from a failing cache (see
#: ``core.caches``); a lost counter would lift the budget.
_BUDGET_KEY = "strict:staff_password_reset:{scope}:{schema}:{digest}"


def can_reset_here(user) -> bool:
    """Whether *user* could sign in to the admin this request reached."""
    if not (user.is_active and user.is_staff and user.has_usable_password()):
        return False
    if user.is_superuser:
        return True
    tenant = get_current_tenant()
    if tenant is None:
        # The platform host admits superusers only.
        return False
    membership = get_membership(user, tenant)
    return membership is not None and membership.is_tenant_staff


def _over_budget(scope: str, identity: str, limit: int) -> bool:
    """Count one request against *identity*'s budget; True once spent.

    Fails closed: a cache that cannot count is a budget that is spent.
    """
    from django.db import connection

    key = _BUDGET_KEY.format(
        scope=scope,
        schema=getattr(connection, "schema_name", "public"),
        digest=hashlib.sha256(identity.encode()).hexdigest(),
    )
    window = settings.STAFF_PASSWORD_RESET["WINDOW_SECONDS"]
    try:
        cache.add(key, 0, window)
        return cache.incr(key) > limit
    except Exception:
        logger.exception("Staff password reset budget unavailable")
        return True


class StaffPasswordResetForm(PasswordResetForm):
    """Django's form, looking in the public schema.

    ``save`` is not used: it would look the address up and send the mail
    inside the request. The view queues
    ``tenant.tasks.send_staff_password_reset_email`` instead, which asks
    this form for the users.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Unfold's widget: Django's bare input has no border under Unfold's
        # CSS, which made the field invisible.
        self.fields["email"].widget = UnfoldAdminEmailInputWidget(
            attrs={"autocomplete": "email", "autofocus": True}
        )

    def get_users(self, email):
        with schema_context(get_public_schema_name()):
            users = list(super().get_users(email))
        return (user for user in users if can_reset_here(user))


class StaffSetPasswordForm(SetPasswordForm):
    """Saves the new password to the public row, and ends the old one's API
    tokens."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Unfold's widgets, for the same reason as the reset form's email.
        for name in ("new_password1", "new_password2"):
            self.fields[name].widget = UnfoldAdminPasswordWidget(
                attrs={"autocomplete": "new-password"}
            )

    def save(self, commit=True):
        from tenant.models import PlatformStaffToken

        with schema_context(get_public_schema_name()):
            user = super().save(commit)
            if commit:
                PlatformStaffToken.objects.filter(user=user).delete()
        return user


class _SiteContextMixin:
    """The admin site's own context (title, branding) on its login-style
    pages, and its URL namespace for ``reverse``."""

    admin_site: Any = None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.admin_site.each_context(self.request))
        context["admin_login_url"] = self._url("login")
        context["password_reset_url"] = self._url("admin_password_reset")
        return context

    def _url(self, name: str, **kwargs: Any) -> str:
        return reverse(
            f"admin:{name}", kwargs=kwargs, current_app=self.admin_site.name
        )


class StaffPasswordResetView(_SiteContextMixin, PasswordResetView):
    form_class = StaffPasswordResetForm
    template_name = "admin/password_reset/form.html"
    title = _("Reset your password")

    def get_success_url(self):
        return self._url("admin_password_reset_done")

    def post(self, request, *args, **kwargs):
        budget = settings.STAFF_PASSWORD_RESET
        if _over_budget("ip", client_ip_or_peer(request), budget["PER_IP"]):
            form = self.get_form()
            form.add_error(None, _("Too many requests. Try again in a while."))
            return self.render_to_response(
                self.get_context_data(form=form), status=429
            )
        return super().post(request, *args, **kwargs)

    def form_valid(self, form) -> HttpResponse:
        email = form.cleaned_data["email"].strip().lower()
        if not _over_budget(
            "email", email, settings.STAFF_PASSWORD_RESET["PER_EMAIL"]
        ):
            from tenant.tasks import send_staff_password_reset_email

            dispatch_on_commit(
                send_staff_password_reset_email,
                kwargs={
                    "email": email,
                    "host": self.request.get_host(),
                    "secure": self.request.is_secure(),
                    "site_name": self.admin_site.name,
                    "urlconf": getattr(self.request, "urlconf", None)
                    or settings.ROOT_URLCONF,
                },
            )
        # The same answer whether or not the address is an account.
        return HttpResponseRedirect(self.get_success_url())


class StaffPasswordResetDoneView(_SiteContextMixin, PasswordResetDoneView):
    template_name = "admin/password_reset/done.html"
    title = _("Check your email")


class StaffPasswordResetConfirmView(
    _SiteContextMixin, PasswordResetConfirmView
):
    form_class = StaffSetPasswordForm
    template_name = "admin/password_reset/confirm.html"
    title = _("Choose a new password")
    # A reset proves the mailbox, not the second factor: the user signs in
    # afterwards, through the login form that asks for the code.
    post_reset_login = False

    def get_success_url(self):
        return self._url("admin_password_reset_complete")

    def get_user(self, uidb64):
        with schema_context(get_public_schema_name()):
            user = super().get_user(uidb64)
        if user is None or not can_reset_here(user):
            return None
        return user


class StaffPasswordResetCompleteView(
    _SiteContextMixin, PasswordResetCompleteView
):
    template_name = "admin/password_reset/complete.html"
    title = _("Password changed")


class StaffPasswordResetMixin:
    """Mounts the flow on an admin site, ahead of the site's catch-all."""

    def get_urls(self):
        site = {"admin_site": self}
        return [
            path(
                "password_reset/",
                StaffPasswordResetView.as_view(**site),
                name="admin_password_reset",
            ),
            path(
                "password_reset/done/",
                StaffPasswordResetDoneView.as_view(**site),
                name="admin_password_reset_done",
            ),
            path(
                "reset/<uidb64>/<token>/",
                StaffPasswordResetConfirmView.as_view(**site),
                name="admin_password_reset_confirm",
            ),
            path(
                "reset/done/",
                StaffPasswordResetCompleteView.as_view(**site),
                name="admin_password_reset_complete",
            ),
            *super().get_urls(),
        ]
