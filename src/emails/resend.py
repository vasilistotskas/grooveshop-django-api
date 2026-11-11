"""The Resend HTTP transport: ``POST https://api.resend.com/emails``.

One request per message, authenticated by the key of the team that owns the
sending domain: a store that sends from its own verified domain uses the key
on its ``Tenant`` row, every other store and the platform's own mail use
``settings.RESEND_API_KEY``. There is no fallback between the two, because
the wrong team cannot send for the other's domain.

Failures are ``requests`` exceptions, so ``core.retry`` already knows what to
do with them: a timeout, a dropped connection, a 5xx and a 429 are retried by
the calling task, any other 4xx is permanent. Neither the key nor a body is
ever logged or put in an exception message.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from typing import Any

import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from emails import sender
from tenant.credentials import tenant_resend_api_key

API_URL = "https://api.resend.com/emails"
IDEMPOTENCY_HEADER = "Idempotency-Key"
#: The two hidden tags every message carries, which is how a provider event
#: finds its log row. They are tags, never headers: a header is visible.
SCHEMA_TAG = "schema"
LOG_TAG = "log"
#: Resend's own error ``name`` (``validation_error``, ...): a fixed token,
#: safe to log, unlike the message that can quote a field.
_ERROR_NAME = re.compile(r"[a-z_]{1,64}")


class ResendNotConfigured(ImproperlyConfigured):
    """No key for the team that must carry this message. Permanent: not a
    class a task retries."""


def api_key() -> str:
    """The key of the Resend team that owns this message's sending domain.

    A store with its own verified domain needs its own key and never reaches
    for the platform's (a domain the platform's team does not own is refused
    by Resend); anyone else sends through the platform's team.
    """
    if sender.own_sender():
        key = tenant_resend_api_key()
        if not key:
            raise ResendNotConfigured(
                "The store sends from its own verified domain but has no "
                "Resend API key (Tenant.resend_api_key)."
            )
        return key
    if not settings.RESEND_API_KEY:
        raise ResendNotConfigured("RESEND_API_KEY is not set.")
    return settings.RESEND_API_KEY


def send(payload: Mapping[str, Any], *, message_ref: uuid.UUID) -> str:
    """Post *payload* and return Resend's id for the message.

    *message_ref* is the idempotency key, so a request repeated within
    Resend's 24 hour window (a retry after a lost response) cannot send a
    second message.
    """
    response = requests.post(
        API_URL,
        json=payload,
        headers={
            "Authorization": f"Bearer {api_key()}",
            IDEMPOTENCY_HEADER: str(message_ref),
        },
        timeout=settings.EMAIL_SEND_TIMEOUT,
    )
    if not response.ok:
        raise requests.HTTPError(_failure_text(response), response=response)
    return response.json()["id"]


def _failure_text(response: requests.Response) -> str:
    """``Resend answered 422 validation_error``: the status and Resend's
    error name, never the message or the request."""
    name = ""
    try:
        candidate = response.json().get("name", "")
    except ValueError, AttributeError:
        candidate = ""
    if isinstance(candidate, str) and _ERROR_NAME.fullmatch(candidate):
        name = f" {candidate}"
    return f"Resend answered {response.status_code}{name}"
