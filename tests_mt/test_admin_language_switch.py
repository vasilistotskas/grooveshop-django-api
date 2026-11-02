"""An operator can switch the admin's language on either host.

The control plane's admin was mounted at the unprefixed ``admin/`` only.
The urlconf has ``i18n_patterns`` (the shared block), so
``LocaleMiddleware`` answered every unprefixed path in ``LANGUAGE_CODE``
and ignored the ``django_language`` cookie, and the language switcher's
``set_language`` redirects to the translated URL (``/en/admin/``), which
did not exist on that host: the platform admin was Greek-only.

Both hosts, both languages, through the same ``set_language`` view the
Unfold switcher posts to.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import pytest
from django.conf import settings
from django.urls import reverse
from django.utils import translation
from django_tenants.test.client import TenantClient
from django_tenants.utils import get_public_schema_name, schema_context

from tenant.auth_backends import PLATFORM_STAFF_BACKEND_PATH
from tenant.models import (
    TenantDomain,
    TenantMembershipRole,
    UserTenantMembership,
)

pytestmark = pytest.mark.django_db

PLATFORM_DOMAIN = "platform-lang-mt.test"
PUBLIC_URLCONF = settings.PUBLIC_SCHEMA_URLCONF
DEFAULT = settings.LANGUAGE_CODE
OTHER = next(code for code, _ in settings.LANGUAGES if code != DEFAULT)


@pytest.fixture
def platform_client(mt_public_tenant):
    from user.models import UserAccount

    with schema_context(get_public_schema_name()):
        TenantDomain.objects.get_or_create(
            domain=PLATFORM_DOMAIN,
            defaults={"tenant": mt_public_tenant, "is_primary": True},
        )
        operator, _created = UserAccount.objects.get_or_create(
            email="mt-platform-lang-operator@example.com",
            defaults={
                "username": "mtplatformlangoperator",
                "is_staff": True,
                "is_superuser": True,
            },
        )
        client = TenantClient(mt_public_tenant)
        client.force_login(operator, backend=PLATFORM_STAFF_BACKEND_PATH)
    return client


@pytest.fixture
def store_client(mt_tenant):
    from user.models import UserAccount

    with schema_context(get_public_schema_name()):
        user, _created = UserAccount.objects.get_or_create(
            email="mt-store-lang-admin@example.com",
            defaults={"username": "mtstorelangadmin", "is_staff": True},
        )
        membership, _created = UserTenantMembership.objects.update_or_create(
            user=user,
            tenant=mt_tenant,
            defaults={"role": TenantMembershipRole.ADMIN},
        )
    client = TenantClient(mt_tenant)
    with schema_context(mt_tenant.schema_name):
        client.force_login(user, backend=PLATFORM_STAFF_BACKEND_PATH)
    yield client
    with schema_context(get_public_schema_name()):
        membership.delete()


def _index(host: str, language: str) -> str:
    with translation.override(language):
        if host == "platform":
            return reverse("platform_admin:index", urlconf=PUBLIC_URLCONF)
        return reverse("admin:index")


@pytest.fixture(params=["platform", "store"])
def host(request):
    return request.param


@pytest.fixture
def client(host, platform_client, store_client):
    return platform_client if host == "platform" else store_client


def test_each_language_has_its_own_admin_address(host):
    assert _index(host, DEFAULT) == "/admin/"
    assert _index(host, OTHER) == f"/{OTHER}/admin/"


@pytest.mark.parametrize("language", [DEFAULT, OTHER])
def test_the_admin_answers_in_the_language_of_its_address(
    client, host, language
):
    response = client.get(_index(host, language))

    assert response.status_code == 200
    assert response["Content-Language"] == language
    assert f'<html lang="{language}"' in response.content.decode()


def _switch(client, from_url: str, language: str):
    """Press the language's button on ``from_url``, as a browser does.

    The form is read out of the rendered page and posted with exactly the
    fields it carries (and the page as the referrer), so a switcher that
    posts nothing useful fails here instead of in a test's own idea of it.
    """
    html = client.get(from_url).content.decode()
    forms = re.findall(
        r'<form action="([^"]*)"[^>]*>(.*?)</form>', html, flags=re.DOTALL
    )
    for action, body in forms:
        if f'name="language" type="hidden" value="{language}"' in body:
            fields = dict(
                re.findall(r'name="(\w+)" type="hidden" value="([^"]*)"', body)
            )
            break
    else:
        pytest.fail(f"no switcher button for {language!r} on {from_url}")
    fields.pop("csrfmiddlewaretoken", None)
    return client.post(
        action,
        fields,
        headers={
            "referer": f"http://{client.tenant.get_primary_domain().domain}{from_url}"
        },
    )


def test_the_language_switcher_moves_to_the_translated_address(client, host):
    response = _switch(client, _index(host, DEFAULT), OTHER)

    assert response.status_code == 302
    assert urlsplit(response["Location"]).path == _index(host, OTHER)
    page = client.get(_index(host, OTHER))
    assert page.status_code == 200
    assert page["Content-Language"] == OTHER


def test_the_language_switcher_moves_back_to_the_default(client, host):
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = OTHER

    response = _switch(client, _index(host, OTHER), DEFAULT)

    assert response.status_code == 302
    assert urlsplit(response["Location"]).path == _index(host, DEFAULT)
    assert client.get(_index(host, DEFAULT))["Content-Language"] == DEFAULT


def test_the_switcher_offers_every_language(client, host):
    html = client.get(_index(host, DEFAULT)).content.decode()

    for code, _name in settings.LANGUAGES:
        assert f'value="{code}"' in html, code
