"""What a Celery task is allowed to retry.

``autoretry_for=(Exception,)`` retries a bug exactly as often as an
outage: a ``TypeError`` or an ``IntegrityError`` fails the same way on
every attempt, so the retries only delay the failure record and, for a
task with side effects, repeat them. A task retries what a retry can fix
and nothing else, so each decorator names one of the sets below.

Celery matches ``autoretry_for`` on the exception CLASS. Two failure
families cannot be told apart by class, because the verdict is in a
value the exception carries:

* SMTP: ``SMTPConnectError(421)`` is worth another attempt and
  ``SMTPAuthenticationError(535)`` is not, and both are
  ``SMTPResponseException``. RFC 5321 puts the answer in the reply code.
* HTTP: a ``requests.HTTPError`` for a 503 is transient, for a 404 it is
  not.

``is_permanent_failure`` answers both, and ``MonitoredTask.retry``
consults it, so a class-based ``autoretry_for`` and an explicit
``self.retry`` get the same verdict from one place.
"""

from __future__ import annotations

import smtplib
from http import HTTPStatus

import requests
from django.db.utils import InterfaceError, OperationalError
from kombu.exceptions import OperationalError as BrokerOperationalError
from meilisearch.errors import (
    MeilisearchCommunicationError,
    MeilisearchTimeoutError,
)
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

#: A dropped or exhausted database connection. ``IntegrityError`` is
#: deliberately absent: a constraint violation does not resolve on retry.
TRANSIENT_DB_ERRORS: tuple[type[BaseException], ...] = (
    OperationalError,
    InterfaceError,
)

#: Sockets, Redis and the broker being briefly unreachable.
TRANSIENT_NETWORK_ERRORS: tuple[type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    RedisConnectionError,
    RedisTimeoutError,
    BrokerOperationalError,
)

#: The default for a task that only touches the database, the cache and
#: the channel layer.
TRANSIENT_ERRORS: tuple[type[BaseException], ...] = (
    *TRANSIENT_DB_ERRORS,
    *TRANSIENT_NETWORK_ERRORS,
)

#: A ``requests`` call. ``HTTPError`` is listed because
#: ``raise_for_status`` raises it for 5xx too; a 4xx is filtered out by
#: ``is_permanent_failure``.
TRANSIENT_HTTP_ERRORS: tuple[type[BaseException], ...] = (
    requests.ConnectionError,
    requests.Timeout,
    requests.HTTPError,
)

#: ``OSError`` is the whole surface of an SMTP send: every ``smtplib``
#: exception derives from it, and so do the socket, DNS and TLS failures
#: that never reach a reply (``ENETUNREACH`` is a bare ``OSError``, not a
#: ``ConnectionError``). ``is_permanent_failure`` stops the 5xx ones.
TRANSIENT_SMTP_ERRORS: tuple[type[BaseException], ...] = (OSError,)

#: A task that sends mail. It reads and writes the database as well.
MAIL_TRANSIENT_ERRORS: tuple[type[BaseException], ...] = (
    *TRANSIENT_ERRORS,
    *TRANSIENT_SMTP_ERRORS,
)

#: The Meilisearch client failing to reach the server or to wait for it.
#: ``MeiliTaskFailed`` (the engine rejected the write) is not here.
MEILI_TRANSIENT_ERRORS: tuple[type[BaseException], ...] = (
    MeilisearchCommunicationError,
    MeilisearchTimeoutError,
)

#: A task that writes to the search index.
SEARCH_TRANSIENT_ERRORS: tuple[type[BaseException], ...] = (
    *TRANSIENT_ERRORS,
    *MEILI_TRANSIENT_ERRORS,
)

#: 4xx statuses a retry can fix: the server asks us to come back.
_RETRYABLE_CLIENT_STATUSES = frozenset(
    {HTTPStatus.REQUEST_TIMEOUT, HTTPStatus.TOO_MANY_REQUESTS}
)


def smtp_codes(exc: BaseException) -> list[int]:
    """Every SMTP status code carried by ``exc``, if any.

    ``SMTPResponseException`` exposes one on ``smtp_code``.
    ``SMTPRecipientsRefused`` is not one of those: it carries
    ``{address: (code, message)}`` instead, and ``sendmail`` only raises
    it when EVERY recipient was refused, so all of its codes matter.
    A connection-level failure (a dropped socket, a TLS error, a DNS
    failure) carries no code at all, which is itself the signal.
    """
    codes: list[int] = []
    code = getattr(exc, "smtp_code", None)
    if isinstance(code, int):
        codes.append(code)
    recipients = getattr(exc, "recipients", None)
    if isinstance(recipients, dict):
        codes.extend(
            entry[0]
            for entry in recipients.values()
            if isinstance(entry, tuple) and isinstance(entry[0], int)
        )
    return codes


def is_permanent_smtp_failure(exc: BaseException) -> bool:
    """Whether retrying an SMTP failure could ever succeed.

    4xx means "try again", 5xx means "do not". The class is a poor
    proxy: retrying a 535 five times with backoff is how an account gets
    locked by the provider. No code means the conversation never got far
    enough to produce one (socket, DNS, TLS): transient by nature.
    """
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        # The server told us it cannot do what we asked. Another
        # attempt asks the same question.
        return True
    codes = smtp_codes(exc)
    return bool(codes) and all(500 <= code < 600 for code in codes)


def is_permanent_http_failure(exc: BaseException) -> bool:
    """Whether retrying a ``requests.HTTPError`` could ever succeed.

    A 4xx says the request itself is wrong, so it fails again, except
    408 and 429, where the server asks for another attempt. An
    ``HTTPError`` without a response carries no verdict and is retried.
    """
    if not isinstance(exc, requests.HTTPError) or exc.response is None:
        return False
    status = exc.response.status_code
    return 400 <= status < 500 and status not in _RETRYABLE_CLIENT_STATUSES


def is_recipient_refused(exc: BaseException) -> bool:
    """Whether the mail server permanently refused every recipient.

    The synchronous bounce: the address cannot receive mail, so neither
    a retry nor a later send to it will do any good.
    """
    return isinstance(
        exc, smtplib.SMTPRecipientsRefused
    ) and is_permanent_smtp_failure(exc)


def is_permanent_failure(exc: BaseException) -> bool:
    """Whether ``exc`` carries a verdict that a retry cannot change."""
    return is_permanent_smtp_failure(exc) or is_permanent_http_failure(exc)
