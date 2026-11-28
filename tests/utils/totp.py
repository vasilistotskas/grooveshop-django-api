"""A held TOTP step, so a code a test computes is the code allauth checks.

allauth validates a TOTP code against the current 30-second step with a
tolerance of zero steps (``allauth.mfa.totp.internal.auth``). A code a test
computes from the real clock and allauth checks a moment later fall in
different steps whenever a step boundary passes in between, and the
enrolment, the login or the reauthentication then fails at random.
``frozen_totp_clock`` holds the step allauth validates against; compute codes
with ``totp_code`` while it is held.
"""

from __future__ import annotations

import time
from collections.abc import Generator
from contextlib import contextmanager
from unittest.mock import patch

from allauth.mfa import app_settings
from allauth.mfa.totp.internal.auth import format_hotp_value, hotp_value

_held: dict[str, int] = {}


@contextmanager
def frozen_totp_clock() -> Generator[None]:
    """Hold allauth's TOTP step at the current one for the block."""
    _held["counter"] = int(time.time()) // app_settings.TOTP_PERIOD
    try:
        with patch(
            "allauth.mfa.totp.internal.auth.yield_hotp_counters_from_time",
            side_effect=lambda: iter([_held["counter"]]),
        ):
            yield
    finally:
        _held.clear()


def totp_code(secret: str, offset: int = 0) -> str:
    """The code for the held step, or *offset* steps from it."""
    return format_hotp_value(hotp_value(secret, _held["counter"] + offset))


@contextmanager
def totp_step_moved(offset: int) -> Generator[None]:
    """Move the held step by *offset*, so a code can be used after the one
    that enrolled (allauth spends a code once)."""
    start = _held["counter"]
    _held["counter"] = start + offset
    try:
        yield
    finally:
        _held["counter"] = start
