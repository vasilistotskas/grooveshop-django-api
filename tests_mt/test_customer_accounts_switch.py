"""A store without customer accounts, through the real host routing.

With ``CUSTOMER_ACCOUNTS_ENABLED`` off, every route of the store's customer
identity (allauth's headless API and pages, the social providers'
callbacks, the store's OIDC provider and its metadata) answers a bare 404
on that store's host, and a credential a shopper was given before is no
sign-in there. Store staff still sign in, a sibling store and the platform
host keep their accounts, and guests still order. The main suite has no
hosts, so the identity routes are walked here
(``tests/integration/tenant/test_customer_accounts.py`` has the rest).
"""

from __future__ import annotations

import logging
import secrets
from datetime import timedelta

import pytest
from django.db import DatabaseError, transaction
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django_tenants.test.client import TenantClient
from django_tenants.utils import get_public_schema_name, schema_context

from tenant.permissions import REFUSED_MESSAGE

pytestmark = pytest.mark.django_db

SWITCH = "CUSTOMER_ACCOUNTS_ENABLED"
PLATFORM_DOMAIN = "platform-accounts-mt.test"
PASSWORD = secrets.token_urlsafe(16)


def _switch(schema: str, on: bool) -> None:
    from extra_settings.models import Setting

    with schema_context(schema):
        Setting.objects.update_or_create(
            name=SWITCH, defaults={"value_type": "bool", "value_bool": on}
        )


@pytest.fixture
def accounts(mt_tenant):
    """Switch the store's accounts; put them back on afterwards, so the
    settings cache (which the test's rollback does not reach) holds the
    default again."""
    switched = []

    def switch(on: bool, schema: str = mt_tenant.schema_name) -> None:
        _switch(schema, on)
        switched.append(schema)

    yield switch
    for schema in switched:
        _switch(schema, True)


def _identity_urls() -> list[str]:
    """One concrete URL for every view the gate refuses."""
    from core.urls import CUSTOMER_IDENTITY_VIEWS

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
            elif (
                isinstance(pattern, URLPattern)
                and pattern.callback in CUSTOMER_IDENTITY_VIEWS
            ):
                if pattern.name is None:
                    urls.append("/" + prefix + str(pattern.pattern))
                    continue
                urls.append(
                    reverse(
                        ":".join((*namespaces, pattern.name)),
                        kwargs=dict.fromkeys(
                            (*params, *pattern.pattern.regex.groupindex), "1"
                        ),
                    )
                )

    walk(get_resolver().url_patterns)
    return sorted(set(urls))


def _get(client, url: str) -> int:
    """GET *url* in a savepoint, so a view that fails on a bare GET (the
    gate is what is asserted, not the view) does not abort the test's
    transaction for the requests after it."""
    client.raise_request_exception = False
    try:
        with transaction.atomic():
            return client.get(url).status_code
    except DatabaseError:
        return 500


def _refusals(caplog) -> list[str]:
    return [
        record.fields["route"]
        for record in caplog.records
        if record.getMessage() == REFUSED_MESSAGE
        and record.fields["switch"] == SWITCH
    ]


def test_every_identity_route_answers_404_without_accounts(
    mt_tenant, accounts, caplog
):
    accounts(False)
    client = TenantClient(mt_tenant)
    urls = _identity_urls()
    # The language-prefixed form of the pages is the same route.
    urls.append("/en" + reverse("account_login"))

    with caplog.at_level(logging.INFO, logger="tenant.permissions"):
        served = {
            url: status
            for url in urls
            if (status := client.get(url).status_code) != 404
        }

    assert len(urls) > 90
    assert served == {}
    assert len(_refusals(caplog)) == len(urls)


def test_with_accounts_on_the_gate_stays_out(mt_tenant, accounts, caplog):
    accounts(True)
    client = TenantClient(mt_tenant)

    with caplog.at_level(logging.INFO, logger="tenant.permissions"):
        for url in _identity_urls():
            _get(client, url)

    assert _refusals(caplog) == []
    assert _get(client, "/_allauth/app/v1/config") == 200


def test_a_sibling_store_keeps_its_accounts(mt_tenant, mt_tenant_b, accounts):
    accounts(False)

    off = TenantClient(mt_tenant).get("/_allauth/app/v1/config")
    on = TenantClient(mt_tenant_b).get("/_allauth/app/v1/config")

    assert off.status_code == 404
    assert on.status_code == 200


@pytest.fixture
def platform_client(mt_public_tenant):
    from tenant.models import TenantDomain

    with schema_context(get_public_schema_name()):
        TenantDomain.objects.get_or_create(
            domain=PLATFORM_DOMAIN,
            defaults={"tenant": mt_public_tenant, "is_primary": True},
        )
    return TenantClient(mt_public_tenant)


def test_the_platform_host_is_never_gated(platform_client, accounts, caplog):
    """Even with the switch set off on the public schema: the platform is
    not a store, so the gate stays out of it. The identity routes are not
    mounted there at all (``test_platform_host_routes``): a plain routing
    404, never a refusal the gate logged."""
    accounts(False, schema=get_public_schema_name())

    with caplog.at_level(logging.INFO, logger="tenant.permissions"):
        statuses = [
            _get(platform_client, url)
            for url in ("/accounts/login/", "/_allauth/app/v1/config")
        ]

    assert _refusals(caplog) == []
    assert statuses == [404, 404]


@pytest.fixture
def shopper_token(mt_tenant):
    """A shopper of the store and the Knox token their sign-in gave them,
    before the store switched its accounts off."""
    from knox.models import AuthToken

    from user.models import UserAccount

    with schema_context(mt_tenant.schema_name):
        shopper = UserAccount.objects.create_user(
            email="mt-accounts-shopper@example.com", password=PASSWORD
        )
        _instance, token = AuthToken.objects.create(shopper)
    return shopper, token


@pytest.mark.parametrize("on", [True, False], ids=["accounts-on", "off"])
def test_a_token_from_before_the_switch_off_is_refused(
    mt_tenant, accounts, shopper_token, on
):
    shopper, token = shopper_token
    accounts(on)
    client = TenantClient(mt_tenant)
    bearer = {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    account = client.get(
        reverse("user-account-detail", kwargs={"pk": shopper.pk}), **bearer
    )
    ticket = client.post(reverse("websocket-ticket"), **bearer)
    # A page a guest may read is still served: the token is ignored, not
    # an error.
    catalogue = client.get(reverse("product-list"), **bearer)

    if on:
        assert account.status_code == 200
        assert ticket.status_code in (200, 201)
    else:
        assert account.status_code == 401
        assert ticket.status_code == 401
    assert catalogue.status_code == 200


@pytest.mark.parametrize("on", [True, False], ids=["accounts-on", "off"])
def test_a_session_from_before_the_switch_off_is_refused(
    mt_tenant, accounts, shopper_token, on
):
    shopper, _token = shopper_token
    accounts(on)
    client = TenantClient(mt_tenant)
    with schema_context(mt_tenant.schema_name):
        client.force_login(
            shopper, backend="django.contrib.auth.backends.ModelBackend"
        )

    response = client.get(
        reverse("user-account-detail", kwargs={"pk": shopper.pk})
    )

    assert response.status_code == (200 if on else 401)


def test_store_staff_sign_in_to_the_admin_without_accounts(mt_tenant, accounts):
    from tenant.enum import TenantMembershipRole
    from tenant.models import UserTenantMembership
    from user.models import UserAccount

    accounts(False)
    with schema_context(get_public_schema_name()):
        staff = UserAccount.objects.create_user(
            email="mt-accounts-staff@example.com",
            password=PASSWORD,
            is_staff=True,
        )
        UserTenantMembership.objects.create(
            user=staff,
            tenant=mt_tenant,
            role=TenantMembershipRole.OWNER,
            is_active=True,
        )
    client = TenantClient(mt_tenant)

    page = client.get("/admin/login/")
    login = client.post(
        "/admin/login/",
        {
            "step": "password",
            "username": staff.email,
            "password": PASSWORD,
            "next": "/admin/",
        },
    )

    assert page.status_code == 200
    assert login.status_code == 302, login.content
    assert client.get("/admin/").status_code == 200


def test_a_staff_token_reads_the_customers_without_accounts(
    mt_tenant, accounts
):
    from tenant.enum import StaffTokenScope
    from tenant.models import PlatformStaffToken
    from user.models import UserAccount

    accounts(False)
    with schema_context(get_public_schema_name()):
        operator = UserAccount.objects.create_superuser(
            email="mt-accounts-operator@example.com",
            username="mtaccountsoperator",
            password=PASSWORD,
        )
        instance, token = PlatformStaffToken.objects.create(
            user=operator, expiry=timedelta(hours=1)
        )
        instance.scope = StaffTokenScope.API
        instance.save(update_fields=["scope"])

    response = TenantClient(mt_tenant).get(
        reverse("user-account-list"),
        HTTP_AUTHORIZATION=f"StaffBearer {token}",
    )

    assert response.status_code == 200, response.content


def test_a_guest_reads_their_order_without_accounts(mt_tenant, accounts):
    from order.factories.order import OrderFactory

    accounts(False)
    with schema_context(mt_tenant.schema_name):
        order = OrderFactory(user=None, num_order_items=0)

    response = TenantClient(mt_tenant).get(
        reverse("order-retrieve-by-uuid", kwargs={"uuid": str(order.uuid)})
    )

    assert response.status_code == 200, response.content


@pytest.mark.parametrize("on", [True, False], ids=["accounts-on", "off"])
def test_an_account_order_opens_by_uuid_only_without_accounts(
    mt_tenant, accounts, on
):
    """Its owner cannot sign in on a store without accounts, so the uuid the
    order emails link to opens it (``Order.served_as_guest``). Where
    accounts are on it still needs its owner."""
    from order.factories.order import OrderFactory
    from user.factories.account import UserAccountFactory

    accounts(on)
    with schema_context(mt_tenant.schema_name):
        order = OrderFactory(
            user=UserAccountFactory(num_addresses=0), num_order_items=0
        )

    response = TenantClient(mt_tenant).get(
        reverse("order-retrieve-by-uuid", kwargs={"uuid": str(order.uuid)})
    )

    assert response.status_code == (403 if on else 200), response.content
