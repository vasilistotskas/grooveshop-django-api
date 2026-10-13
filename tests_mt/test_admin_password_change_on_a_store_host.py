"""The admin's own password change, on a STORE host.

A platform-staff identity lives in the public schema, while a store
host binds the store's schema. The page must render under the store's
schema - its sidebar reads the store's tables (the active carriers, for
one), which the public schema does not have - and only the password
save may go to public, where the user's row is.

Running the whole view inside ``schema_context(public)`` answered 500
on every store host from v3.115.0 (``relation
"shipping_shippingprovider" does not exist``). The main suite has a
single schema holding every table, so it cannot see this.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django_tenants.test.client import TenantClient
from django_tenants.utils import get_public_schema_name, schema_context

from tenant.auth_backends import PLATFORM_STAFF_BACKEND_PATH

NEW_PASSWORD = "a-brand-new-passw0rd!"


@pytest.fixture
def operator():
    from user.models import UserAccount

    with schema_context(get_public_schema_name()):
        return UserAccount.objects.create_superuser(
            email="mt-password-operator@example.com",
            username="mtpasswordoperator",
            password="old-passw0rd!",
        )


@pytest.fixture
def client_(mt_tenant, operator):
    client = TenantClient(mt_tenant)
    # The session lives where the request will look for it: the store's
    # schema, as a real sign-in on the store host leaves it.
    with schema_context(mt_tenant.schema_name):
        client.force_login(operator, backend=PLATFORM_STAFF_BACKEND_PATH)
    return client


@pytest.mark.django_db
def test_the_page_renders_on_a_store_host(client_):
    response = client_.get(reverse("admin:password_change"))

    assert response.status_code == 200


@pytest.mark.django_db
def test_the_new_password_lands_on_the_public_row_and_keeps_the_session(
    client_, operator
):
    response = client_.post(
        reverse("admin:password_change"),
        {
            "old_password": "old-passw0rd!",
            "new_password1": NEW_PASSWORD,
            "new_password2": NEW_PASSWORD,
        },
    )

    assert response.status_code == 302
    with schema_context(get_public_schema_name()):
        operator.refresh_from_db()
    assert operator.check_password(NEW_PASSWORD)
    # The session was re-hashed in the store's session table, where it
    # lives, so changing the password did not sign the operator out.
    assert client_.get(reverse("admin:index")).status_code == 200
