"""A staff member's wrong passwords and wrong codes count once, on every host.

Staff are public identities: the platform host and every store host reach
the same account. The cache key function prefixes the ACTIVE schema onto a
key, so a counter kept under a plain key would give each host its own
allowance (five guesses on the platform and five more on each store). The
counters are ``global:`` keys; the main suite has one schema and cannot see
the difference.
"""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django_tenants.utils import get_public_schema_name, schema_context

from admin import staff_mfa
from tenant.auth_backends import PlatformStaffBackend
from tenant.staff_login_limits import StaffLoginLocked

pytestmark = pytest.mark.django_db

PASSWORD = "Platform-wide-passw0rd!"


@pytest.fixture(autouse=True)
def _fresh_counters():
    cache.clear()
    yield
    cache.clear()


def _staff():
    from user.models import UserAccount

    with schema_context(get_public_schema_name()):
        return UserAccount.objects.create_user(
            email="platform-wide-limits@example.com",
            username="platformwidelimits",
            password=PASSWORD,
            is_staff=True,
        )


def test_wrong_passwords_on_every_host_share_one_allowance(
    mt_tenant, mt_tenant_b, settings
):
    settings.STAFF_LOGIN_LIMITS = {
        **settings.STAFF_LOGIN_LIMITS,
        "MAX_FAILURES_PER_ACCOUNT": 4,
    }
    user = _staff()
    backend = PlatformStaffBackend()
    hosts = [
        get_public_schema_name(),
        mt_tenant.schema_name,
        mt_tenant_b.schema_name,
    ]
    limit = settings.STAFF_LOGIN_LIMITS["MAX_FAILURES_PER_ACCOUNT"]
    for attempt in range(limit):
        with schema_context(hosts[attempt % len(hosts)]):
            assert backend.authenticate_staff(None, user.email, "no") is None

    for host in hosts:
        with schema_context(host), pytest.raises(StaffLoginLocked):
            backend.authenticate_staff(None, user.email, PASSWORD)


def test_wrong_codes_on_every_host_share_one_lockout(mt_tenant, mt_tenant_b):
    user = _staff()
    hosts = [mt_tenant.schema_name, mt_tenant_b.schema_name]
    for attempt in range(staff_mfa.MAX_FAILURES):
        with schema_context(hosts[attempt % len(hosts)]):
            with pytest.raises(staff_mfa.SecondFactorWrong):
                staff_mfa.verify_second_factor(user, "000000")

    for host in (get_public_schema_name(), *hosts):
        with schema_context(host):
            assert staff_mfa.is_locked(user)
