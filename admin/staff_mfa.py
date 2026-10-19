"""A second factor for platform superusers' admin login.

A platform superuser reaches every store's data and the control plane, so a
password alone is not enough for that account. The admin login form
(``admin.forms.PlatformAdminAuthenticationForm``) asks for an authenticator
code (TOTP) or a recovery code after the password, only when the user is a
superuser; store admins and staff keep their password-only login.

Everything cryptographic is allauth's own (``allauth.mfa``): the TOTP
secret, code validation, replay protection, recovery codes and their
encryption. This module only decides WHEN a code is required and carries
the enrolment, because the admin login has no allauth login flow to hang
them on. Platform identities live in the public schema, so the
authenticator rows are read and written there (``allauth.mfa`` is in
``SHARED_APPS`` too).

First login: a superuser with no authenticator is not given a session. The
form shows a secret (as a link and a QR code) and asks for a code from the
app; a correct code activates the authenticator, shows the recovery codes
ONCE, and the next login asks for a code like any other.

Not covered: platform staff API tokens (``PlatformStaffToken``) are issued
after a login and are a separate surface.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from allauth.mfa.adapter import get_adapter
from allauth.mfa.models import Authenticator
from allauth.mfa.recovery_codes.internal.auth import RecoveryCodes
from allauth.mfa.totp.internal.auth import (
    TOTP,
    generate_totp_secret,
    validate_totp_code,
)
from django.core.cache import cache
from django.db import transaction
from django_tenants.utils import get_public_schema_name, schema_context

logger = logging.getLogger(__name__)

#: The session key holding the secret of an enrolment in progress, with the
#: user it is for.
ENROLMENT_SESSION_KEY = "staff_mfa.enrolment"

#: Wrong codes allowed per user before further attempts are refused, and how
#: long the refusal lasts. The password is already known at this point, so
#: the code is the only thing left to guess.
MAX_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60


def requires_mfa(user) -> bool:
    """Only platform superusers; store admins and staff stay on a password."""
    return bool(getattr(user, "is_superuser", False))


def _public():
    return schema_context(get_public_schema_name())


def is_enrolled(user) -> bool:
    with _public():
        return Authenticator.objects.filter(
            user_id=user.pk, type=Authenticator.Type.TOTP
        ).exists()


def _failures_key(user) -> str:
    return f"staff_mfa.failures:{user.pk}"


def is_locked(user) -> bool:
    return cache.get(_failures_key(user), 0) >= MAX_FAILURES


def record_failure(user) -> None:
    key = _failures_key(user)
    cache.add(key, 0, timeout=LOCKOUT_SECONDS)
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=LOCKOUT_SECONDS)
    logger.warning("Wrong staff MFA code", extra={"user_id": user.pk})


def clear_failures(user) -> None:
    cache.delete(_failures_key(user))


def verify_code(user, code: str) -> bool:
    """Whether *code* is a current authenticator code or an unused recovery
    code of *user* (a recovery code is spent by being used)."""
    code = (code or "").strip().replace(" ", "")
    if not code:
        return False
    with _public():
        for authenticator in Authenticator.objects.filter(
            user_id=user.pk,
            type__in=(
                Authenticator.Type.TOTP,
                Authenticator.Type.RECOVERY_CODES,
            ),
        ):
            if authenticator.wrap().validate_code(code):
                authenticator.record_usage()
                return True
    return False


@dataclass(frozen=True)
class Enrolment:
    secret: str
    url: str


def begin_enrolment(request, user) -> Enrolment:
    """The secret of the enrolment in progress for *user*, made on the first
    call and kept in the session so a second submit checks the same one."""
    state = request.session.get(ENROLMENT_SESSION_KEY)
    if not state or state.get("user_id") != user.pk:
        state = {"user_id": user.pk, "secret": generate_totp_secret()}
        request.session[ENROLMENT_SESSION_KEY] = state
    return Enrolment(
        secret=state["secret"],
        url=get_adapter().build_totp_url(user, state["secret"]),
    )


def complete_enrolment(request, user, code: str) -> list[str] | None:
    """Activate the authenticator when *code* matches the secret of the
    enrolment in progress, and return the recovery codes (shown once);
    ``None`` for a wrong code."""
    state = request.session.get(ENROLMENT_SESSION_KEY)
    if not state or state.get("user_id") != user.pk:
        return None
    secret = state["secret"]
    if not validate_totp_code(secret, (code or "").strip().replace(" ", "")):
        return None
    with _public(), transaction.atomic():
        # The code that enrolled is spent: it cannot also log in.
        TOTP.activate(user, secret).validate_code(code.strip().replace(" ", ""))
        recovery = RecoveryCodes.activate(user)
        codes = recovery.generate_codes()
    request.session.pop(ENROLMENT_SESSION_KEY, None)
    return codes


def reset(user) -> int:
    """Remove every authenticator of *user*; the next login enrols again.
    Returns how many rows went."""
    with _public():
        deleted, _ = Authenticator.objects.filter(user_id=user.pk).delete()
    clear_failures(user)
    return deleted
