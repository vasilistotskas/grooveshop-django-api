from __future__ import annotations

from django import forms
from django.core.exceptions import NON_FIELD_ERRORS
from django.utils.translation import gettext_lazy as _
from django_tenants.utils import get_public_schema_name, schema_context
from unfold.forms import AdminOwnPasswordChangeForm, AuthenticationForm
from unfold.widgets import (
    UnfoldAdminCheckboxSelectMultipleWidget,
    UnfoldAdminTextInputWidget,
    UnfoldBooleanSwitchWidget,
)

from admin import staff_mfa
from admin.staff_mfa import Step
from core.utils.qr import qr_svg
from tenant.auth_backends import (
    PLATFORM_STAFF_BACKEND_PATH,
    PlatformStaffBackend,
)


class PlatformAdminAuthenticationForm(AuthenticationForm):
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

    A platform SUPERUSER also needs a second factor (``admin.staff_mfa``),
    so the sign-in is several posts of this one form, tied together by the
    session and not by re-posting the password. ``step`` is the one the
    page shows; a step change is not an error, so only a real failure (a
    wrong password or code, a lockout, an expired sign-in) puts anything
    in ``errors``:

    - ``password``: username and password. A store admin or staff member
      is signed in here, as always; a superuser moves on.
    - ``code``: an authenticator or recovery code (already enrolled).
    - ``enrol``: the secret to set up, and its first code (not yet).
    - ``recovery``: the recovery codes, shown once; confirming them
      completes the sign-in.

    Until a sign-in is complete ``is_valid()`` is false and ``get_user()``
    is ``None``, so ``LoginView`` re-renders the page for the next step
    instead of logging anyone in.
    """

    otp_code = forms.CharField(
        label=_("Authenticator or recovery code"),
        required=False,
        strip=True,
        widget=UnfoldAdminTextInputWidget(
            attrs={
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
                "autofocus": True,
                "spellcheck": "false",
                "class": "text-center font-mono text-2xl tracking-[0.35em]",
            }
        ),
    )

    #: Posted by the page next to a step's own fields so a post that
    #: arrives after the sign-in expired can be told from a first one.
    STEP_FIELD = "step"
    #: The name of the button that abandons the sign-in in progress.
    RESTART_FIELD = "restart"

    def __init__(self, request=None, *args, **kwargs):
        super().__init__(request, *args, **kwargs)
        self.signed_in = False
        self._pending_user = None
        self.pending = staff_mfa.get_pending(request)
        self.step = self.pending.step if self.pending else Step.PASSWORD
        self.submitted_step = Step.PASSWORD
        if self.is_bound:
            self.submitted_step = self._submitted_step()
            # Only the step being answered asks for its fields.
            restarting = self.RESTART_FIELD in self.data
            asks_password = (
                self.submitted_step is Step.PASSWORD and not restarting
            )
            asks_code = (
                self.submitted_step in (Step.CODE, Step.ENROL)
                and not restarting
            )
            self.fields["username"].required = asks_password
            self.fields["password"].required = asks_password
            self.fields["otp_code"].required = asks_code

    def _submitted_step(self) -> Step:
        try:
            return Step(self.data.get(self.STEP_FIELD) or Step.PASSWORD)
        except ValueError:
            return Step.PASSWORD

    @property
    def step_number(self) -> int:
        """1-based position of ``step``; the recovery codes are the last
        of the three."""
        return {
            Step.PASSWORD: 1,
            Step.CODE: 2,
            Step.ENROL: 2,
            Step.RECOVERY: 3,
        }[self.step]

    @property
    def step_total(self) -> int:
        """Two steps for a user who has an authenticator, three for one
        who is setting it up."""
        return 2 if self.step is Step.CODE else 3

    @property
    def step_numbers(self) -> range:
        return range(1, self.step_total + 1)

    @property
    def has_field_errors(self) -> bool:
        return any(name != NON_FIELD_ERRORS for name in self.errors)

    @property
    def account(self) -> str:
        """Who the sign-in in progress is for, shown so a wrong account is
        noticed before a code is spent on it."""
        user = self._user_in_progress()
        return user.get_username() if user else ""

    @property
    def enrolment(self):
        if self.step is not Step.ENROL or self._user_in_progress() is None:
            return None
        return staff_mfa.begin_enrolment(self.request, self._pending_user)

    @property
    def enrolment_qr(self) -> str:
        enrolment = self.enrolment
        return qr_svg(enrolment.url) if enrolment else ""

    @property
    def recovery_codes(self) -> tuple[str, ...]:
        if self.step is Step.RECOVERY and self.pending:
            return self.pending.recovery_codes
        return ()

    def _user_in_progress(self):
        """The user the sign-in in progress is for, read again from the
        public schema each time a step is shown or answered, so a
        deactivated account stops where it is."""
        if self._pending_user is None and self.pending:
            self._pending_user = PlatformStaffBackend().get_user(
                self.pending.user_id
            )
        return self._pending_user

    def is_valid(self) -> bool:
        return super().is_valid() and self.signed_in

    def clean(self):
        if self.RESTART_FIELD in self.data:
            self._abandon()
            return self.cleaned_data
        if self.submitted_step is Step.PASSWORD:
            self._password_step()
            return self.cleaned_data
        user = self._user_in_progress()
        if user is None or not staff_mfa.requires_mfa(user):
            self._abandon()
            raise forms.ValidationError(
                _("Your sign-in has expired. Log in again."),
                code="mfa_expired",
            )
        if self.submitted_step is not self.step:
            # A page from an earlier step (another tab): show where the
            # sign-in really is, without answering anything.
            return self.cleaned_data
        if self.step is Step.RECOVERY:
            self._finish(user)
        elif "otp_code" in self.cleaned_data:
            self._code_step(user)
        return self.cleaned_data

    def _abandon(self) -> None:
        staff_mfa.clear_pending(self.request)
        self.pending = None
        self._pending_user = None
        self.step = Step.PASSWORD

    def _password_step(self) -> None:
        # Whoever signs in here ends any sign-in in progress, so a
        # different user can never inherit the half-finished one.
        self._abandon()
        username = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")
        if username is None or not password:
            return
        user = PlatformStaffBackend().authenticate_staff(
            self.request, username=username, password=password
        )
        if user is None:
            raise self.get_invalid_login_error()
        # Bind the session to PlatformStaffBackend explicitly —
        # django.contrib.auth.login() falls back to
        # ``user.backend`` when no explicit backend kwarg is given.
        user.backend = PLATFORM_STAFF_BACKEND_PATH
        self.confirm_login_allowed(user)
        if not staff_mfa.requires_mfa(user):
            self._finish(user)
            return
        self.pending = staff_mfa.begin_pending(self.request, user)
        self._pending_user = user
        self.step = self.pending.step

    def _code_step(self, user) -> None:
        code = self.cleaned_data["otp_code"]
        if staff_mfa.is_locked(user):
            raise forms.ValidationError(
                _("Too many wrong codes. Try again in a few minutes."),
                code="mfa_locked",
            )
        if self.step is Step.CODE:
            if not staff_mfa.verify_code(user, code):
                self._wrong_code(user)
            staff_mfa.clear_failures(user)
            self._finish(user)
            return
        codes = staff_mfa.complete_enrolment(self.request, user, code)
        if codes is None:
            self._wrong_code(user)
        # Both factors are proven, but the recovery codes are shown once
        # before the sign-in completes.
        self.pending = staff_mfa.advance_pending(
            self.request, self.pending, Step.RECOVERY, codes
        )
        self.step = Step.RECOVERY

    def _wrong_code(self, user) -> None:
        staff_mfa.record_failure(user)
        raise forms.ValidationError(
            _("That code is not correct."), code="mfa_wrong_code"
        )

    def _finish(self, user) -> None:
        staff_mfa.clear_pending(self.request)
        self.pending = None
        user.backend = PLATFORM_STAFF_BACKEND_PATH
        self.confirm_login_allowed(user)
        self.user_cache = user
        self.signed_in = True


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
