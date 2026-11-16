"""A signed-out visitor of a staff page is sent to the host's admin login.

The editor's image upload and Rosetta are staff pages outside the admin.
They used to send a signed-out visitor to ``LOGIN_URL``, allauth's
SHOPPER sign-in: a page the platform host does not serve, and on a store
host one that signs a staff member in as a customer of the store, who may
not upload. They now name ``STAFF_LOGIN_URL``, the admin login of the site
the host serves, reversed in the host's own URL conf. The main suite has
one URL conf and no hosts, so only this lane can see which login a host
reaches.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from django.conf import settings
from django.db import connection
from django.urls import reverse
from django_tenants.test.client import TenantClient
from django_tenants.utils import get_public_schema_name, schema_context

pytestmark = pytest.mark.django_db

PLATFORM_DOMAIN = "platform-staff-pages-mt.test"
#: The editor's upload, Rosetta's file list, its form (our
#: ``DBBackedTranslationFormView``) and its suggestion endpoint.
STAFF_PAGES = [
    "/upload_image",
    "/rosetta/files/project/",
    "/rosetta/files/project/el/0/",
    "/rosetta/translate/",
]


@pytest.fixture(autouse=True)
def _back_to_public():
    yield
    # A request through ``TenantClient`` leaves the connection on the
    # store's schema.
    connection.set_schema_to_public()


@pytest.fixture
def platform(mt_public_tenant):
    from tenant.models import TenantDomain

    with schema_context(get_public_schema_name()):
        TenantDomain.objects.get_or_create(
            domain=PLATFORM_DOMAIN,
            defaults={"tenant": mt_public_tenant, "is_primary": True},
        )
    return TenantClient(mt_public_tenant)


@pytest.fixture
def store(mt_tenant):
    return TenantClient(mt_tenant)


def _sent_to(response) -> tuple[str, str]:
    assert response.status_code == 302, response.status_code
    location = urlsplit(response["Location"])
    return location.path, parse_qs(location.query)["next"][0]


@pytest.mark.parametrize("page", STAFF_PAGES)
def test_the_platform_host_sends_to_the_platform_admin_login(platform, page):
    login = reverse(
        "platform_admin:login", urlconf=settings.PUBLIC_SCHEMA_URLCONF
    )

    path, next_page = _sent_to(platform.get(page))

    assert (path, next_page) == (login, page)
    assert platform.get(login).status_code == 200


@pytest.mark.parametrize("page", STAFF_PAGES)
def test_a_store_host_sends_to_the_store_admin_login(store, page):
    login = reverse("admin:login", urlconf=settings.ROOT_URLCONF)

    path, next_page = _sent_to(store.get(page))

    assert (path, next_page) == (login, page)
    assert path != settings.LOGIN_URL
    assert store.get(login).status_code == 200
