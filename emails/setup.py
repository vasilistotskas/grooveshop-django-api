"""Is a store's email setup complete for the transport in use?

Over SMTP every store sends through the platform's relay, so nothing is
store-specific. Over Resend a store that sends from its own verified domain
needs the key of its own team (``emails.resend.api_key``); one without it
cannot send at all. This is the one definition of that gap, read by the
deploy guard (``emails.checks``), the tenant's own validation and the two
dashboards, so none of them can disagree.
"""

from __future__ import annotations

from django.conf import settings
from django_tenants.utils import get_public_schema_name

from emails.models import EmailProvider
from tenant.models import Tenant


def resend_is_selected() -> bool:
    return settings.EMAIL_TRANSPORT == EmailProvider.RESEND


def has_own_sender(tenant: Tenant) -> bool:
    """Whether *tenant* sends as its own verified domain (``from_email``
    set and ``from_email_verified``): the fact ``sender.own_sender`` and the
    Resend key choice both rest on."""
    return bool(tenant.from_email.strip()) and tenant.from_email_verified


def lacks_resend_key(tenant: Tenant) -> bool:
    """Whether *tenant*'s mail cannot be sent through Resend: its own
    verified domain and no key of its own."""
    return (
        resend_is_selected()
        and has_own_sender(tenant)
        and not tenant.resend_api_key
    )


def stores_lacking_resend_key() -> list[Tenant]:
    """The active, not suspended stores that :func:`lacks_resend_key`,
    in one query on ``public``; ``[]`` unless Resend is selected. An empty
    encrypted value stays empty in the column, so the filter is in SQL."""
    if not resend_is_selected():
        return []
    return list(
        Tenant.objects.exclude(schema_name=get_public_schema_name())
        .filter(
            is_active=True,
            suspended_at__isnull=True,
            from_email_verified=True,
            resend_api_key="",
        )
        .exclude(from_email="")
        .order_by("schema_name")
    )


def sender_domain(tenant: Tenant) -> str:
    """``webside.gr`` for ``orders@webside.gr``."""
    return tenant.from_email.strip().rpartition("@")[2]
