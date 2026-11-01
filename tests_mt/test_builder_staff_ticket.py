"""A page builder ticket belongs to the store it was minted on.

The ticket is a cache key, and the cache key function puts the active schema
in front of every key, so a ticket minted for store A finds nothing when it
is spent on store B: replay across stores is structural, not a check.
"""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django_tenants.utils import schema_context

from builder import staff_session
from tenant.auth_backends import PLATFORM_IDENTITY_ATTR


class _Staff:
    pk = 7


setattr(_Staff, PLATFORM_IDENTITY_ATTR, True)


@pytest.mark.django_db
def test_a_ticket_cannot_be_spent_on_another_store(mt_tenant, mt_tenant_b):
    with schema_context(mt_tenant.schema_name):
        ticket = staff_session.mint_ticket(_Staff())

    with schema_context(mt_tenant_b.schema_name):
        assert staff_session._consume_ticket(ticket) is None

    with schema_context(mt_tenant.schema_name):
        assert staff_session._consume_ticket(ticket) == _Staff.pk
        assert staff_session._consume_ticket(ticket) is None


@pytest.mark.django_db
def test_the_key_names_the_store(mt_tenant, mt_tenant_b):
    keys = []
    for tenant in (mt_tenant, mt_tenant_b):
        with schema_context(tenant.schema_name):
            keys.append(
                cache.make_and_validate_key(
                    f"{staff_session.TICKET_CACHE_PREFIX}same"
                )
            )

    assert keys[0] != keys[1]
