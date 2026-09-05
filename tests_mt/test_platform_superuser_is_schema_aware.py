"""The MT lane is where this rule is real.

The main suite runs single-schema on `public`, so `is_platform_superuser`
takes its public-schema branch there and every existing test keeps its
superuser ergonomics. This lane runs the actual tenant router and
middleware, which is the only place the distinction the predicate draws
can be observed end to end.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django_tenants.utils import get_public_schema_name

from tenant.auth_backends import PLATFORM_IDENTITY_ATTR
from tenant.membership import is_platform_superuser, is_store_staff
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


def test_a_tenant_schema_superuser_is_not_a_platform_superuser(mt_tenant):
    """Residue on a customer row must not read as platform authority."""
    connection.set_tenant(mt_tenant)
    try:
        residue = UserAccountFactory(is_superuser=True)

        assert connection.schema_name == mt_tenant.schema_name
        assert is_platform_superuser(residue) is False
        assert is_store_staff(residue) is False
    finally:
        connection.set_schema_to_public()


def test_a_stamped_identity_is_trusted_on_a_tenant(mt_tenant):
    connection.set_tenant(mt_tenant)
    try:
        operator = UserAccountFactory(is_superuser=True)
        setattr(operator, PLATFORM_IDENTITY_ATTR, True)

        assert is_platform_superuser(operator) is True
    finally:
        connection.set_schema_to_public()


def test_the_public_schema_flag_means_what_it_says():
    """On public, `user_useraccount` IS the platform table."""
    connection.set_schema_to_public()

    assert connection.schema_name == get_public_schema_name()
    assert is_platform_superuser(UserAccountFactory(is_superuser=True)) is True


def test_a_plain_customer_is_neither(mt_tenant):
    connection.set_tenant(mt_tenant)
    try:
        shopper = UserAccountFactory(is_superuser=False, is_staff=True)

        assert is_platform_superuser(shopper) is False
        assert is_store_staff(shopper) is False
    finally:
        connection.set_schema_to_public()
