from __future__ import annotations

from django import forms
from django.contrib.admin.forms import AdminAuthenticationForm
from django.utils.translation import gettext_lazy as _
from unfold.widgets import (
    UnfoldAdminCheckboxSelectMultipleWidget,
    UnfoldBooleanSwitchWidget,
)

from tenant.auth_backends import (
    PLATFORM_STAFF_BACKEND_PATH,
    PlatformStaffBackend,
)


class PlatformAdminAuthenticationForm(AdminAuthenticationForm):
    """Admin login form — authenticates ONLY against ``PlatformStaffBackend``.

    Bypasses Django's global ``django.contrib.auth.authenticate()``
    dispatch (which walks ``settings.AUTHENTICATION_BACKENDS`` in
    order) and instead calls ``PlatformStaffBackend.authenticate_staff()``
    directly. Admin sessions are platform-staff-only — there is no
    tenant-schema login path: a tenant-schema customer's credentials
    never resolve here because the backend only ever looks at the
    PUBLIC schema's user table.

    On the public-schema host (the platform admin) the effect is
    identical — ``schema_context()`` is a no-op when already connected
    to that schema.
    """

    def clean(self):
        username = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")

        if username is not None and password:
            self.user_cache = PlatformStaffBackend().authenticate_staff(
                self.request, username=username, password=password
            )
            if self.user_cache is None:
                raise self.get_invalid_login_error()
            # Bind the session to PlatformStaffBackend explicitly —
            # django.contrib.auth.login() falls back to
            # ``user.backend`` when no explicit backend kwarg is given.
            self.user_cache.backend = PLATFORM_STAFF_BACKEND_PATH
            self.confirm_login_allowed(self.user_cache)

        return self.cleaned_data


class CachePurgeForm(forms.Form):
    """The cache management page: which surfaces to purge, and whether
    their related surfaces go too. Rendered through Unfold's widgets,
    and validated, so an unknown surface code is refused here."""

    surfaces = forms.MultipleChoiceField(
        required=False, widget=UnfoldAdminCheckboxSelectMultipleWidget
    )
    include_related = forms.BooleanField(
        label=_("Include related surfaces"),
        required=False,
        initial=True,
        widget=UnfoldBooleanSwitchWidget,
    )

    def __init__(self, *args, surfaces, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["surfaces"].choices = [
            (surface.code, surface.label) for surface in surfaces
        ]
