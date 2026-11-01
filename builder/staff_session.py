"""How a staff member gets from the store admin into the storefront editor.

The editor runs on the store's own host, where no one is signed in as staff:
staff identities live in the public schema, a storefront visitor's session is
a customer's. So the admin hands over a **ticket** and the storefront trades
it for a short-lived **staff token**:

1. In the store admin ("Edit storefront"), ``mint_ticket`` stores the staff
   member's id under a random key in Redis for ``BUILDER_STAFF_TICKET_TTL_
   SECONDS`` and the browser is sent to ``https://<store>/_builder?ticket=``.
2. The storefront's SERVER posts the ticket to ``POST builder/staff-session``.
   ``open_session`` consumes it atomically (``GETDEL``: two requests with the
   same ticket cannot both win), checks the person still may edit this
   store's pages, and mints a ``PlatformStaffToken`` of scope ``builder``.
3. The token is the storefront's to keep in its own HttpOnly cookie; it names
   no more than the builder's routes, is not renewed by use and ends at its
   expiry (``BUILDER_STAFF_SESSION_TTL_SECONDS``).

This is the single-use ticket of the WebSocket (``core.middleware.channels``)
applied to staff. The cache key is per store (the cache key function adds the
schema), so a ticket minted on one store finds nothing on another.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.utils.translation import gettext_lazy as _
from django_tenants.utils import get_public_schema_name, schema_context

from builder.errors import BuilderError
from builder.services import CHANGE_PERMISSION
from tenant.auth_backends import PLATFORM_IDENTITY_ATTR
from tenant.membership import get_current_tenant, get_membership
from tenant.models import PlatformStaffToken, StaffTokenScope
from tenant.staff_actors import resolve_staff, staff_id

TICKET_CACHE_PREFIX = "builder:staff-ticket:"

#: ``role`` in the session answer for a platform superuser, who holds no
#: membership.
SUPERUSER_ROLE = "superuser"


class InvalidTicket(BuilderError):
    default_code = "invalid_ticket"
    default_detail = _("The ticket is not valid.")


def mint_ticket(user: Any) -> str:
    """A single-use ticket for *user*, a staff identity of the store being
    served, valid for ``BUILDER_STAFF_TICKET_TTL_SECONDS``."""
    ticket = secrets.token_urlsafe(32)
    cache.set(
        f"{TICKET_CACHE_PREFIX}{ticket}",
        staff_id(user),
        settings.BUILDER_STAFF_TICKET_TTL_SECONDS,
    )
    return ticket


def _consume_ticket(ticket: str) -> int | None:
    """The staff id a ticket names, deleting it in the same round trip.

    ``GETDEL`` is atomic where a ``get`` then ``delete`` is not: of two
    concurrent requests with one ticket, one gets the value and the other
    nothing.
    """
    key = cache.make_and_validate_key(f"{TICKET_CACHE_PREFIX}{ticket}")
    raw = cache._cache.get_client(key, write=True).getdel(key)
    try:
        return int(raw) if raw is not None else None
    except ValueError, TypeError:
        return None


@dataclass(frozen=True, slots=True)
class Session:
    token: str
    expires_at: datetime
    staff_id: int
    name: str
    role: str


def _role_of(user: Any) -> str:
    if user.is_superuser:
        return SUPERUSER_ROLE
    membership = get_membership(user, get_current_tenant())
    return membership.role if membership is not None else ""


def open_session(ticket: str) -> Session:
    """Trade a ticket for a builder session token.

    Everything is checked when the ticket is spent, not when it was
    minted: the person must still be an active staff member who may change
    this store's pages (the role backend, through the provenance stamp).
    Any failure is the same ``invalid_ticket``, so a probe learns nothing.
    """
    ticket_owner = _consume_ticket(ticket) if ticket else None
    user = (
        resolve_staff([ticket_owner]).get(ticket_owner)
        if ticket_owner
        else None
    )
    if user is None or not (user.is_active and user.is_staff):
        raise InvalidTicket()
    setattr(user, PLATFORM_IDENTITY_ATTR, True)
    if not user.has_perm(CHANGE_PERMISSION):
        raise InvalidTicket()

    with schema_context(get_public_schema_name()):
        instance, token = PlatformStaffToken.objects.create(
            user,
            expiry=timedelta(
                seconds=settings.BUILDER_STAFF_SESSION_TTL_SECONDS
            ),
            scope=StaffTokenScope.BUILDER,
        )
    expires_at = instance.expiry
    assert expires_at is not None  # a session always has an expiry
    return Session(
        token=token,
        expires_at=expires_at,
        staff_id=user.pk,
        name=user.get_full_name().strip() or user.get_username(),
        role=_role_of(user),
    )
