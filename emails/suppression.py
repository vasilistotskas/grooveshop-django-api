"""The suppression list: addresses a store (or the platform) must not write
to again.

A row is written from a provider event (``emails.events``): a hard bounce
suppresses every email, a spam complaint only marketing (owner decision D7:
a complaint about a newsletter is no reason to withhold a receipt). Rows
hold a hash and a masked form only, and are kept until removed.
"""

from __future__ import annotations

from emails import optout
from emails.models import EmailLog, EmailSuppression
from emails.types import EmailType

Reason = EmailSuppression.Reason
Scope = EmailSuppression.Scope


def blocks(address: str, email_type: EmailType) -> bool:
    """Whether *address* is suppressed for this type: ``ALL`` blocks every
    type, ``MARKETING`` only a marketing one."""
    scopes = [Scope.ALL]
    if email_type.opt_out_applies:
        scopes.append(Scope.MARKETING)
    return EmailSuppression.objects.filter(
        address_hash=optout.address_hash(address), scope__in=scopes
    ).exists()


def suppress(log: EmailLog, *, reason: str, scope: str) -> None:
    """Suppress the recipient of *log*. A wider scope replaces a narrower
    one (a bounce after a complaint), never the reverse. A row without a
    recipient (a skip) has no address to suppress."""
    if not log.recipient_hash:
        return
    row, created = EmailSuppression.objects.get_or_create(
        address_hash=log.recipient_hash,
        defaults={
            "recipient_masked": log.recipient_masked,
            "reason": reason,
            "scope": scope,
            "source_log": log,
        },
    )
    if not created and row.scope == Scope.MARKETING and scope == Scope.ALL:
        row.scope = Scope.ALL
        row.reason = reason
        row.source_log = log
        row.save(update_fields=["scope", "reason", "source_log", "updated_at"])
