"""The ``From`` and ``Reply-To`` of an email.

Which store's identity applies is decided by the schema ``send_email`` runs
under: a platform type is sent from ``public``, so the same function yields
the platform's sender there.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import connection

from emails.setup import has_own_sender
from emails.types import Audience, EmailType, Owner
from tenant.credentials import (
    in_tenant_schema,
    tenant_contact_email,
    tenant_site_name,
)
from tenant.membership import bound_tenant


def own_sender() -> str:
    """The bound store's own ``From`` address, ``""`` when it has none to
    use: only a store whose domain is VERIFIED on the relay (``from_email``
    set AND ``from_email_verified``) sends as itself. The same fact decides
    which Resend team carries the mail (``emails.resend.api_key``)."""
    if not in_tenant_schema():
        return ""
    tenant = bound_tenant()
    if tenant is not None and has_own_sender(tenant):
        return tenant.from_email.strip()
    return ""


def from_address() -> str:
    """Return the outbound ``From`` header for the active tenant,
    deliverability-safe.

    An address is only usable in ``From`` when the relay can
    authenticate it, so the sending domain — not the merchant's
    preference — decides. Policy:

    - Tenant whose own domain is VERIFIED on the relay
      (``from_email`` set AND ``from_email_verified``): RFC 5322
      display-name form ``"{store name}" <from_email>``. The relay
      holds a DKIM key for that domain, so the message is signed and
      DMARC-aligned. This is the white-label case.
    - Any other tenant: ``"{store name}" <DEFAULT_FROM_EMAIL>`` —
      authenticated platform envelope, tenant brand in the inbox sender
      line. The tenant's own address belongs in ``Reply-To`` (send
      sites already pass ``tenant_contact_email()``).
    - Public schema (platform/admin sends): ``"{SITE_NAME}"
      <DEFAULT_FROM_EMAIL>``.
    - No tenant bound to the connection at all: bare
      ``DEFAULT_FROM_EMAIL``.

    The verification flag IS the safety property. Putting a merchant's
    address in ``From`` on a relay that cannot sign for that domain
    fails DMARC alignment and lands the mail in spam — not
    hypothetically: ``DEFAULT_FROM_EMAIL`` was itself set to a tenant's
    address (``info@webside.gr``) on a relay authorised for neither
    SPF nor DKIM, so EVERY tenant's mail went out unauthenticated, and
    under tenant #1's domain, until 2026-09-13.

    ``DEFAULT_FROM_EMAIL`` must therefore be a PLATFORM address. The
    flag is platform-only — deliberately absent from
    ``TENANT_SELF_EDITABLE_FIELDS``, so a store operator can propose a
    ``from_email`` but cannot certify it.
    """
    from email.utils import formataddr

    address = own_sender() or getattr(settings, "DEFAULT_FROM_EMAIL", "") or ""

    if not address:
        # No sender configured at all — an empty address inside a
        # display-name form ("Store <>") would be invalid RFC 5322.
        return ""
    # Any binding (a store row, or the public schema's FakeTenant, which
    # ``bound_tenant`` reads as None) carries a display name; only a
    # connection with no tenant at all sends the bare address.
    name = tenant_site_name()
    if name and getattr(connection, "tenant", None) is not None:
        return formataddr((name, address))
    return address


def store_reply_to() -> list[str]:
    """Return the ``Reply-To`` list for outbound mail from the active store.

    ``[tenant_contact_email()]`` when the store has a contact address,
    ``[]`` when it has none. ``EmailMessage`` joins whatever list it is
    given, so ``[""]`` would put a literal empty ``Reply-To:`` header on
    the wire — an RFC 5322 violation some receivers score as spam —
    whereas an empty list emits no header at all and replies go to the
    authenticated platform sender.
    """
    email = tenant_contact_email()
    return [email] if email else []


def reply_to(email_type: EmailType, obj: Any) -> list[str]:
    """Where replies go: what the type itself names for *obj* (a form
    submission is answered to its submitter), else the store's contact
    address for a store's mail to its customers, nobody otherwise. The
    platform's mail is not answered in a store's name, and an alert to the
    store's own staff has no one to reply to (it would come back to the
    staff's own inbox)."""
    if email_type.reply_to is not None:
        return list(email_type.reply_to(obj))
    if email_type.owner is Owner.STORE and (
        email_type.audience is Audience.CUSTOMER
    ):
        return store_reply_to()
    return []
