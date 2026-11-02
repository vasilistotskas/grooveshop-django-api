"""A staff token authenticates on a store host, through the real schemas.

Staff tokens (``tenant.PlatformStaffToken``) live in the PUBLIC schema and
customer tokens (``knox.AuthToken``) in each store's, so ``knox_authtoken``
does not exist in ``public``. The main suite cannot see that: it runs every
app's tables in ``public`` (``tests/conftest.py``), where the knox table is
present. Until ``PlatformStaffTokenAuthentication`` stopped inheriting knox's
``_cleanup_token``, which walks the knox table, every ``StaffBearer``
request answered 500 here and in production, while the main suite stayed
green.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from django_tenants.test.client import TenantClient
from django_tenants.utils import get_public_schema_name, schema_context

pytestmark = pytest.mark.django_db


@pytest.fixture
def operator():
    from user.models import UserAccount

    # The test's transaction takes the row away again.
    with schema_context(get_public_schema_name()):
        return UserAccount.objects.create_superuser(
            email="mt-staff-token@example.com",
            username="mtstafftoken",
            password="a-passw0rd!",
        )


def _staff_token(user, *, scope: str, expiry: timedelta | None = None):
    from tenant.models import PlatformStaffToken

    with schema_context(get_public_schema_name()):
        instance, token = PlatformStaffToken.objects.create(
            user=user, expiry=expiry
        )
        instance.scope = scope
        instance.save(update_fields=["scope"])
    return instance, token


def test_the_layout_this_lane_runs_on_has_no_knox_table_in_public():
    """The premise: what production has, and the main suite does not."""
    from django.db import connection

    with schema_context(get_public_schema_name()):
        tables = connection.introspection.table_names()

    assert "tenant_platformstafftoken" in tables
    assert "knox_authtoken" not in tables


def test_a_builder_token_reads_the_store_documents(mt_tenant, operator):
    from tenant.models import StaffTokenScope

    _instance, token = _staff_token(
        operator, scope=StaffTokenScope.BUILDER, expiry=timedelta(hours=1)
    )

    response = TenantClient(mt_tenant).get(
        reverse("builder:document-list"),
        HTTP_AUTHORIZATION=f"StaffBearer {token}",
    )

    assert response.status_code == 200, response.content


def test_authenticating_purges_the_staff_members_expired_tokens(
    mt_tenant, operator
):
    from tenant.models import PlatformStaffToken, StaffTokenScope

    expired, _old = _staff_token(
        operator, scope=StaffTokenScope.BUILDER, expiry=timedelta(hours=1)
    )
    with schema_context(get_public_schema_name()):
        PlatformStaffToken.objects.filter(pk=expired.pk).update(
            expiry=timezone.now() - timedelta(minutes=1)
        )
    live, token = _staff_token(
        operator, scope=StaffTokenScope.BUILDER, expiry=timedelta(hours=1)
    )

    response = TenantClient(mt_tenant).get(
        reverse("builder:document-list"),
        HTTP_AUTHORIZATION=f"StaffBearer {token}",
    )

    assert response.status_code == 200, response.content
    with schema_context(get_public_schema_name()):
        remaining = set(
            PlatformStaffToken.objects.filter(user=operator).values_list(
                "pk", flat=True
            )
        )
    assert remaining == {live.pk}


def test_an_expired_token_is_refused_and_deleted(mt_tenant, operator):
    from tenant.models import PlatformStaffToken, StaffTokenScope

    instance, token = _staff_token(
        operator, scope=StaffTokenScope.BUILDER, expiry=timedelta(hours=1)
    )
    with schema_context(get_public_schema_name()):
        PlatformStaffToken.objects.filter(pk=instance.pk).update(
            expiry=timezone.now() - timedelta(minutes=1)
        )

    response = TenantClient(mt_tenant).get(
        reverse("builder:document-list"),
        HTTP_AUTHORIZATION=f"StaffBearer {token}",
    )

    assert response.status_code == 401
    with schema_context(get_public_schema_name()):
        assert not PlatformStaffToken.objects.filter(pk=instance.pk).exists()


def test_signing_out_of_the_editor_ends_the_token(mt_tenant, operator):
    from tenant.models import PlatformStaffToken, StaffTokenScope

    instance, token = _staff_token(
        operator, scope=StaffTokenScope.BUILDER, expiry=timedelta(hours=1)
    )
    client = TenantClient(mt_tenant)

    response = client.delete(
        reverse("builder:staff-session"),
        HTTP_AUTHORIZATION=f"StaffBearer {token}",
    )

    assert response.status_code == 204, response.content
    with schema_context(get_public_schema_name()):
        assert not PlatformStaffToken.objects.filter(pk=instance.pk).exists()
