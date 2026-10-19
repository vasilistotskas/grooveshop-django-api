from __future__ import annotations

from django import forms
from django.contrib.admin.forms import AdminAuthenticationForm
from django.utils.translation import gettext_lazy as _
from django_tenants.utils import get_public_schema_name, schema_context
from unfold.forms import AdminOwnPasswordChangeForm
from unfold.widgets import (
    UnfoldAdminCheckboxSelectMultipleWidget,
    UnfoldAdminTextInputWidget,
    UnfoldBooleanSwitchWidget,
)

from admin import staff_mfa
from core.utils.qr import qr_svg
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

    A platform SUPERUSER also needs a second factor (``admin.staff_mfa``):
    the password is checked first, then an authenticator or recovery code.
    One without an authenticator gets no session until they enrol one, in
    this same form. ``mfa_stage`` tells the template which step it is on.
    """

    otp_code = forms.CharField(
        label=_("Authenticator code"),
        required=False,
        strip=True,
        widget=UnfoldAdminTextInputWidget(
            attrs={"autocomplete": "one-time-code", "inputmode": "numeric"}
        ),
    )

    #: ``None`` (password only), ``"code"`` (asks for a code), ``"enrol"``
    #: (shows the secret to set up) or ``"recovery"`` (shows the recovery
    #: codes once, after enrolment).
    mfa_stage: str | None = None
    enrolment = None
    recovery_codes: list[str] | None = None

    @property
    def enrolment_qr(self) -> str:
        return qr_svg(self.enrolment.url) if self.enrolment else ""

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
            if staff_mfa.requires_mfa(self.user_cache):
                self._second_factor(self.user_cache)

        return self.cleaned_data

    def _second_factor(self, user) -> None:
        """Let *user* through only with a correct code; otherwise leave no
        authenticated user behind and say what is needed."""
        code = self.cleaned_data.get("otp_code", "")
        enrolled = staff_mfa.is_enrolled(user)
        self.mfa_stage = "code" if enrolled else "enrol"
        if not enrolled:
            self.enrolment = staff_mfa.begin_enrolment(self.request, user)

        def refuse(message):
            self.user_cache = None
            raise forms.ValidationError(message, code="mfa")

        if not code:
            refuse(
                _("Enter the code from your authenticator app.")
                if enrolled
                else _(
                    "Set up your authenticator app with the secret below, "
                    "then enter the code it shows."
                )
            )
        if staff_mfa.is_locked(user):
            refuse(_("Too many wrong codes. Try again in a few minutes."))
        if enrolled:
            if not staff_mfa.verify_code(user, code):
                staff_mfa.record_failure(user)
                refuse(_("That code is not correct."))
            staff_mfa.clear_failures(user)
            self.mfa_stage = None
            return
        codes = staff_mfa.complete_enrolment(self.request, user, code)
        if codes is None:
            staff_mfa.record_failure(user)
            refuse(_("That code is not correct."))
        # Enrolled, but no session yet: the recovery codes are shown once
        # and the next login asks for a code like any other.
        self.mfa_stage = "recovery"
        self.recovery_codes = codes
        self.enrolment = None
        refuse(
            _(
                "Authenticator set up. Save the recovery codes below, "
                "then log in again with a code."
            )
        )


class PlatformPasswordChangeForm(AdminOwnPasswordChangeForm):
    """Unfold's own password-change form, saved to the PUBLIC schema.

    An admin session is always a platform identity whose row lives in
    the public schema (``PlatformStaffBackend``), while the page runs
    under the schema the host resolved — a store's, on a store host,
    where that row does not exist. Only the save needs public: the page
    itself (the store's sidebar reads the store's tables) and the
    session it re-hashes (the store's session table) belong to the
    store.
    """

    def save(self, commit=True):
        with schema_context(get_public_schema_name()):
            return super().save(commit)


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
