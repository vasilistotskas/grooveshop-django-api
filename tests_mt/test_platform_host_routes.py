"""The platform host answers no 500 to an anonymous GET, on any route.

The platform host is the PUBLIC schema, which holds only the control
plane's tables: a page that reads a store's table there (allauth's sign-in
lists the social providers, ``socialaccount_socialapp``, which exists in
store schemas only) answers 500 where the right answer is a 404. The main
suite runs every app in one schema and cannot see that, so this walks the
platform URL conf through the real host routing.
"""

from __future__ import annotations

import contextlib

import pytest
from django.conf import settings
from django.db import DatabaseError, transaction
from django.urls import (
    NoReverseMatch,
    URLPattern,
    URLResolver,
    get_resolver,
    reverse,
)
from django_tenants.test.client import TenantClient
from django_tenants.utils import get_public_schema_name, schema_context

pytestmark = pytest.mark.django_db

PLATFORM_DOMAIN = "platform-routes-mt.test"
PLATFORM_URLCONF = settings.PUBLIC_SCHEMA_URLCONF

#: A route that answers 500 on purpose, and why.
DELIBERATE_500 = {
    # Viva's verification GET: with no Viva account on the platform there is
    # no key to answer, and the receiver says so (``order.views.viva_webhook``)
    # rather than answer Viva with an empty key.
    "/viva-wallet/webhook/": "no Viva account on the platform host",
}


@pytest.fixture
def platform_client(mt_public_tenant):
    from tenant.models import TenantDomain

    with schema_context(get_public_schema_name()):
        TenantDomain.objects.get_or_create(
            domain=PLATFORM_DOMAIN,
            defaults={"tenant": mt_public_tenant, "is_primary": True},
        )
    client = TenantClient(mt_public_tenant)
    client.raise_request_exception = False
    return client


def _status(client, url: str) -> int:
    """GET *url* in a savepoint, so a view that breaks the connection's
    transaction does not hide the routes after it. A view that swallows a
    failed query leaves the savepoint unreleasable: what it answered is
    still the answer."""
    response = None
    try:
        with transaction.atomic():
            response = client.get(url)
    except DatabaseError:
        if response is None:
            return 500
    return response.status_code


def _platform_urls() -> list[str]:
    urls = []

    def walk(patterns, prefix="", namespaces=(), params=()):
        for pattern in patterns:
            if isinstance(pattern, URLResolver):
                walk(
                    pattern.url_patterns,
                    prefix + str(pattern.pattern),
                    namespaces
                    + ((pattern.namespace,) if pattern.namespace else ()),
                    (*params, *pattern.pattern.regex.groupindex),
                )
            elif isinstance(pattern, URLPattern):
                if pattern.name is None:
                    continue
                with contextlib.suppress(NoReverseMatch):
                    urls.append(
                        reverse(
                            ":".join((*namespaces, pattern.name)),
                            urlconf=PLATFORM_URLCONF,
                            kwargs=dict.fromkeys(
                                (*params, *pattern.pattern.regex.groupindex),
                                "1",
                            ),
                        )
                    )

    walk(get_resolver(PLATFORM_URLCONF).url_patterns)
    return sorted(set(urls))


def test_the_platform_sign_in_page_is_not_served(platform_client):
    assert _status(platform_client, "/accounts/login/") == 404
    assert _status(platform_client, "/en/accounts/login/") == 404


def test_no_platform_route_answers_500_to_an_anonymous_get(platform_client):
    urls = _platform_urls()
    failing = {
        url: status
        for url in urls
        if url not in DELIBERATE_500
        and (status := _status(platform_client, url)) >= 500
    }

    assert failing == {}
    assert len(urls) > 100, len(urls)
