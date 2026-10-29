"""The ``From`` and ``Reply-To`` of an email.

The rules live in ``tenant.credentials`` for now and are reused, not
copied; the migration of the existing send sites moves them here. Which
store's identity applies is decided by the schema ``send_email`` runs
under: a platform type is sent from ``public``, so the same function yields
the platform's sender there.
"""

from __future__ import annotations

from emails.types import Owner
from tenant.credentials import tenant_from_email, tenant_reply_to


def from_address() -> str:
    """The DMARC-safe ``From`` of the active schema: the store's verified
    own domain when it has one, else its name on the platform's sender."""
    return tenant_from_email()


def reply_to(owner: Owner) -> list[str]:
    """Where replies go: the store's contact address for a store's mail,
    nobody for the platform's (its mail is not answered in a store's name)."""
    return tenant_reply_to() if owner is Owner.STORE else []
