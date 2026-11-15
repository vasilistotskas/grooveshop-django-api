"""A store can run without customer accounts (``CUSTOMER_ACCOUNTS_ENABLED``).

What the switch takes away, in the single-schema lane: the features that
need an account read as off and their routes refuse, the services and jobs
that act for account holders stand down, and a credential a shopper was
given before the switch-off is no longer a sign-in. The routes of the
identity surface itself (allauth, the store's OIDC provider) are refused by
``tenant.middleware.CustomerAccountsMiddleware``, which only engages on a
store host: ``tests_mt/test_customer_accounts_switch.py`` walks them on the
real host routing. ``docs/store-features.md``, "Customer accounts".
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from knox.models import AuthToken
from rest_framework import status
from rest_framework.test import APIClient, APIRequestFactory

from blog.factories.post import BlogPostFactory
from product.factories.product import ProductFactory
from tests.utils.store_settings import switch_store_settings
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

#: Every switch the dependent features need, on, so the only thing
#: turning them off below is the accounts switch.
THEIR_OWN_SWITCHES = {
    "CATALOGUE_ENABLED": True,
    "FAVOURITES_ENABLED": True,
    "PRODUCT_REVIEWS_ENABLED": True,
    "ACCOUNT_REVIEWS_ENABLED": True,
    "BLOG_COMMENTS_ENABLED": True,
    "LOYALTY_ENABLED": True,
    "B2B_WHOLESALE_ENABLED": True,
    "AGENT_COMMERCE_ENABLED": True,
}


@pytest.fixture
def shopper():
    return UserAccountFactory(num_addresses=0)


def _store_host(bind_tenant_and_schema) -> None:
    """Be on a store's host: a tenant bound, the connection on its schema
    name (the tables stay where this lane keeps them)."""
    bind_tenant_and_schema(SimpleNamespace(schema_name="no_accounts_store"))


# --- the features that need an account ---------------------------------

# (route, kwargs owner, signed in): a route of each feature that needs an
# account, including the ones serving its data from somewhere else.
DEPENDENT_ROUTES = [
    ("product-favourite-list", None, True),
    ("user-account-favourite-products", "account", True),
    ("product-review-list", None, False),
    ("product-reviews", "product", False),
    ("user-account-product-reviews", "account", True),
    ("blog-comment-list", None, False),
    ("blog-post-comments", "post", False),
    ("user-account-blog-post-comments", "account", True),
    ("loyalty:loyalty-summary", None, True),
    ("loyalty:loyalty-tiers", None, False),
    ("b2b:b2b-prices", None, True),
    ("agent-favourites", None, False),
    ("agent-loyalty", None, False),
]


def _url(route: str, owner: str | None, shopper) -> str:
    if owner == "product":
        pk = ProductFactory(active=True, num_images=0, num_reviews=0).pk
    elif owner == "post":
        pk = BlogPostFactory(num_tags=0, num_comments=0).pk
    elif owner == "account":
        pk = shopper.pk
    else:
        return reverse(route)
    return reverse(route, kwargs={"pk": pk})


@pytest.mark.parametrize("on", [True, False], ids=["accounts-on", "off"])
@pytest.mark.parametrize(
    ("route", "owner", "signed_in"),
    DEPENDENT_ROUTES,
    ids=[route.split(":")[-1] for route, *_ in DEPENDENT_ROUTES],
)
def test_a_feature_that_needs_an_account_goes_with_the_accounts(
    route, owner, signed_in, on, shopper
):
    switch_store_settings(**THEIR_OWN_SWITCHES, CUSTOMER_ACCOUNTS_ENABLED=on)
    url = _url(route, owner, shopper)
    client = APIClient()
    if signed_in:
        client.force_authenticate(user=shopper)

    response = client.get(url)

    assert (response.status_code != status.HTTP_404_NOT_FOUND) is on, (
        response.status_code
    )


@pytest.mark.parametrize("on", [True, False])
def test_the_services_of_loyalty_and_wholesale_follow_the_accounts(on):
    from b2b.services import B2BService
    from loyalty.services import LoyaltyService

    switch_store_settings(**THEIR_OWN_SWITCHES, CUSTOMER_ACCOUNTS_ENABLED=on)

    assert LoyaltyService.is_enabled() is on
    assert (LoyaltyService.active_points_rates() is not None) is on
    assert B2BService.is_enabled() is on


def test_switching_accounts_off_pauses_the_loyalty_programme():
    from loyalty.models import LoyaltyPause
    from loyalty.services import LoyaltyService

    switch_store_settings(**THEIR_OWN_SWITCHES, CUSTOMER_ACCOUNTS_ENABLED=False)

    LoyaltyService.sync_pause()

    assert LoyaltyPause.objects.filter(ended_at__isnull=True).exists()


def test_the_accounts_switch_queues_the_loyalty_pause_sync(
    bind_tenant_and_schema,
):
    from unittest.mock import call

    from loyalty.tasks import sync_loyalty_pause

    bind_tenant_and_schema(SimpleNamespace(schema_name="shop_one"))
    switch_store_settings(CUSTOMER_ACCOUNTS_ENABLED=True)

    with patch("tenant.switches.dispatch_on_commit") as dispatch:
        switch_store_settings(CUSTOMER_ACCOUNTS_ENABLED=False)

    assert [
        c for c in dispatch.call_args_list if c.args[0] is sync_loyalty_pause
    ] == [call(sync_loyalty_pause, schema_name="shop_one")]


@pytest.mark.parametrize(
    "task_name",
    [
        "notify_back_in_stock_favourites_live",
        "send_price_drop_notifications",
    ],
)
def test_the_favourites_notifications_stand_down_without_accounts(task_name):
    from product import tasks

    switch_store_settings(**THEIR_OWN_SWITCHES, CUSTOMER_ACCOUNTS_ENABLED=False)
    product = ProductFactory(num_images=0, num_reviews=0)
    task = getattr(tasks, task_name)
    args = (
        (product.id,)
        if task_name == "notify_back_in_stock_favourites_live"
        else (product.id, 10.0, 5.0)
    )

    result = task.run(*args)

    assert result == {"status": "skipped", "reason": "favourites_disabled"}


@pytest.mark.parametrize(
    "key",
    [
        "cart.checkout_abandoned",
        "user.data_export_ready",
        "user.reengagement",
        "account.email_confirmation_signup",
        "account.password_reset_key",
    ],
)
@pytest.mark.parametrize("on", [True, False])
def test_mail_for_an_account_holder_is_not_sent_without_accounts(key, on):
    from emails.registry import registry

    switch_store_settings(
        **THEIR_OWN_SWITCHES, CART_ENABLED=True, CUSTOMER_ACCOUNTS_ENABLED=on
    )
    email_type = registry.get(key)

    accounts = [
        gate
        for gate in email_type.gates
        if getattr(gate, "feature", None) == "customer_accounts"
    ]

    assert len(accounts) == 1
    assert accounts[0].is_open() is on


# --- a credential given before the switch-off ----------------------------


def _bearer_request(token: str):
    return APIRequestFactory().get(
        "/api/v1/product", HTTP_AUTHORIZATION=f"Bearer {token}"
    )


@pytest.mark.parametrize("on", [True, False])
def test_a_customer_token_is_no_sign_in_without_accounts(
    on, shopper, bind_tenant_and_schema
):
    from rest_framework.request import Request

    from core.api.tokens import BoundedTokenAuthentication

    switch_store_settings(CUSTOMER_ACCOUNTS_ENABLED=on)
    _store_host(bind_tenant_and_schema)
    _instance, token = AuthToken.objects.create(shopper)

    result = BoundedTokenAuthentication().authenticate(
        Request(_bearer_request(token))
    )

    assert (result is not None and result[0] == shopper) is on


def test_an_ignored_token_is_not_renewed(shopper, bind_tenant_and_schema):
    """Left to expire (decision 2), so presenting it must not push its
    expiry forward the way a sign-in does."""
    from rest_framework.request import Request

    from core.api.tokens import BoundedTokenAuthentication

    switch_store_settings(CUSTOMER_ACCOUNTS_ENABLED=False)
    _store_host(bind_tenant_and_schema)
    instance, token = AuthToken.objects.create(shopper)
    expiry = instance.expiry

    BoundedTokenAuthentication().authenticate(Request(_bearer_request(token)))

    instance.refresh_from_db()
    assert instance.expiry == expiry


@pytest.mark.parametrize("on", [True, False])
def test_an_account_endpoint_refuses_a_token_from_before_the_switch_off(
    on, shopper, bind_tenant_and_schema
):
    switch_store_settings(CUSTOMER_ACCOUNTS_ENABLED=on)
    _store_host(bind_tenant_and_schema)
    _instance, token = AuthToken.objects.create(shopper)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    response = client.get(
        reverse("user-account-detail", kwargs={"pk": shopper.pk})
    )

    expected = status.HTTP_200_OK if on else status.HTTP_401_UNAUTHORIZED
    assert response.status_code == expected, response.data


@pytest.mark.parametrize("on", [True, False])
def test_a_customer_session_is_no_sign_in_without_accounts(
    on, shopper, bind_tenant_and_schema
):
    from rest_framework.request import Request

    from tenant.customer_accounts import StoreSessionAuthentication

    switch_store_settings(CUSTOMER_ACCOUNTS_ENABLED=on)
    _store_host(bind_tenant_and_schema)
    django_request = APIRequestFactory().get("/api/v1/product")
    django_request.user = shopper
    django_request._dont_enforce_csrf_checks = True

    result = StoreSessionAuthentication().authenticate(Request(django_request))

    assert (result is not None and result[0] == shopper) is on


def test_a_staff_session_is_untouched_by_the_customer_switch(
    bind_tenant_and_schema,
):
    from rest_framework.request import Request

    from tenant.customer_accounts import StoreSessionAuthentication
    from tests.utils.staff import stamp_platform_identity

    switch_store_settings(CUSTOMER_ACCOUNTS_ENABLED=False)
    _store_host(bind_tenant_and_schema)
    staff = stamp_platform_identity(UserAccountFactory(is_staff=True))
    django_request = APIRequestFactory().get("/api/v1/product")
    django_request.user = staff
    django_request._dont_enforce_csrf_checks = True

    result = StoreSessionAuthentication().authenticate(Request(django_request))

    assert result is not None and result[0] == staff


def test_the_agent_token_is_not_even_looked_at_without_accounts(
    bind_tenant_and_schema,
):
    from rest_framework.request import Request

    from agent.views import AgentTokenAuthentication

    switch_store_settings(CUSTOMER_ACCOUNTS_ENABLED=False)
    _store_host(bind_tenant_and_schema)

    with patch("agent.views.get_server") as server:
        result = AgentTokenAuthentication().authenticate(
            Request(_bearer_request("an-oidc-access-token"))
        )

    assert result is None
    server.assert_not_called()


@pytest.mark.parametrize("on", [True, False])
def test_the_cart_agent_token_dies_with_the_accounts(
    on, shopper, bind_tenant_and_schema
):
    """It is derived from a Knox sign-in (``docs/cart-agent-token.md``)."""
    from core.api.tokens import is_auth_token_live

    switch_store_settings(CUSTOMER_ACCOUNTS_ENABLED=on)
    _store_host(bind_tenant_and_schema)
    instance, _token = AuthToken.objects.create(shopper)

    assert is_auth_token_live(shopper.pk, instance.digest) is on


@pytest.mark.parametrize("on", [True, False])
def test_a_websocket_ticket_from_before_the_switch_off_is_refused(
    on, shopper, bind_tenant_and_schema
):
    from core import tickets
    from core.middleware.channels import _authenticate_ticket_inner

    switch_store_settings(CUSTOMER_ACCOUNTS_ENABLED=on)
    _store_host(bind_tenant_and_schema)
    AuthToken.objects.create(shopper)
    ticket = tickets.mint(tickets.WEBSOCKET, shopper.pk, 60)

    user = _authenticate_ticket_inner(ticket, AuthToken)

    assert (user == shopper) is on
    if not on:
        assert isinstance(user, AnonymousUser)


def test_the_platform_host_is_never_gated(shopper):
    """On the public schema every identity is the platform's own."""
    from rest_framework.request import Request

    from core.api.tokens import BoundedTokenAuthentication

    _instance, token = AuthToken.objects.create(shopper)
    switch_store_settings(CUSTOMER_ACCOUNTS_ENABLED=False)

    result = BoundedTokenAuthentication().authenticate(
        Request(_bearer_request(token))
    )

    assert result is not None and result[0] == shopper


# --- the identity routes ------------------------------------------------

#: Where the store's customer identity is served: allauth's headless API,
#: its pages (with the social providers' callbacks), the OIDC provider and
#: its metadata.
IDENTITY_PREFIXES = ("_allauth/", "accounts/", "identity/", ".well-known/")


def _routes(patterns, prefix=""):
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _routes(
                pattern.url_patterns, prefix + str(pattern.pattern)
            )
        elif isinstance(pattern, URLPattern):
            yield prefix + str(pattern.pattern), pattern.callback


def _is_identity_view(route: str, callback) -> bool:
    view_class = getattr(callback, "view_class", None)
    modules = [getattr(callback, "__module__", "")]
    if view_class is not None:
        modules += [klass.__module__ for klass in view_class.__mro__]
    return route.startswith(IDENTITY_PREFIXES) or any(
        module.startswith("allauth.") for module in modules
    )


@pytest.mark.parametrize("on", [True, False], ids=["accounts-on", "off"])
def test_a_store_host_refuses_the_identity_routes(
    on, bind_tenant_and_schema, caplog
):
    import logging

    from tenant.permissions import REFUSED_MESSAGE

    switch_store_settings(CUSTOMER_ACCOUNTS_ENABLED=on)
    _store_host(bind_tenant_and_schema)

    with caplog.at_level(logging.INFO, logger="tenant.permissions"):
        response = APIClient().get("/_allauth/app/v1/config")

    refusals = [
        record.fields
        for record in caplog.records
        if record.getMessage() == REFUSED_MESSAGE
    ]
    if on:
        assert response.status_code == status.HTTP_200_OK
        assert refusals == []
    else:
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert refusals == [
            {
                "gate": "CustomerAccountsMiddleware",
                "switch": "CUSTOMER_ACCOUNTS_ENABLED",
                "route": "_allauth/app/v1/config",
                "method": "GET",
            }
        ]


def test_the_platform_host_keeps_its_identity_routes():
    """The public schema, with the switch set off there all the same."""
    switch_store_settings(CUSTOMER_ACCOUNTS_ENABLED=False)

    response = APIClient().get("/_allauth/app/v1/config")

    assert response.status_code == status.HTTP_200_OK


def test_every_identity_route_of_a_store_is_gated():
    """A new allauth endpoint, or a route mounted under one of the identity
    prefixes, is refused without anyone listing it; one that is not gated
    fails here."""
    from core.urls import CUSTOMER_IDENTITY_VIEWS

    identity = {
        route: callback
        for route, callback in _routes(get_resolver().url_patterns)
        # The admin's changelists of the provider's clients and tokens are
        # the store staff's, not a shopper's.
        if not route.startswith("admin/") and _is_identity_view(route, callback)
    }

    ungated = sorted(
        route
        for route, callback in identity.items()
        if callback not in CUSTOMER_IDENTITY_VIEWS
    )
    assert len(identity) > 90
    assert ungated == []


def test_the_gate_takes_nothing_but_identity_routes():
    from core.urls import CUSTOMER_IDENTITY_VIEWS

    gated_elsewhere = sorted(
        route
        for route, callback in _routes(get_resolver().url_patterns)
        if callback in CUSTOMER_IDENTITY_VIEWS
        and not _is_identity_view(route, callback)
    )

    assert gated_elsewhere == []


def test_every_agent_route_for_a_linked_shopper_is_gated():
    """``/api/v1/agent/*`` minus the gateway's internal read and the
    store team's own surface: every view acting for a shopper is an
    ``AgentAPIView``, which refuses while accounts are off."""
    from agent.views import AgentAPIView
    from tenant.permissions import IsCustomerAccountsEnabled

    customer_views = [
        callback.view_class
        for route, callback in _routes(get_resolver().url_patterns)
        if route.startswith("api/v1/agent/")
        and not route.startswith(
            ("api/v1/agent/staff/", "api/v1/agent/internal/")
        )
    ]

    assert len(customer_views) == 4
    for view_class in customer_views:
        assert issubclass(view_class, AgentAPIView), view_class
        gates = [type(p) for p in view_class().get_permissions()]
        assert IsCustomerAccountsEnabled in gates, view_class


# --- guest checkout -----------------------------------------------------


@pytest.mark.usefixtures("checkout_legal_documents")
def test_a_guest_checks_out_on_a_store_without_accounts(
    shopper, bind_tenant_and_schema
):
    """And a stale token riding along does not stop them: it is ignored,
    so the order is a guest order."""
    from cart.factories import CartFactory, CartItemFactory
    from country.factories import CountryFactory
    from order.models.order import Order
    from pay_way.enum.settlement import PaySettlement
    from pay_way.factories import PayWayFactory
    from region.factories import RegionFactory
    from tests.utils.shipping import enable_rate

    country = CountryFactory(num_regions=0)
    region = RegionFactory(country=country)
    enable_rate(country)
    pay_way = PayWayFactory.create_offline_payment(
        provider_code="cash", settlement=PaySettlement.COURIER_CASH
    )
    product = ProductFactory(stock=5, num_images=0, num_reviews=0, active=True)
    cart = CartFactory(user=None)
    CartItemFactory(cart=cart, product=product, quantity=1)
    _instance, token = AuthToken.objects.create(shopper)
    switch_store_settings(
        CATALOGUE_ENABLED=True,
        CART_ENABLED=True,
        CUSTOMER_ACCOUNTS_ENABLED=False,
    )
    _store_host(bind_tenant_and_schema)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    response = client.post(
        reverse("order-list"),
        {
            "email": "guest@example.com",
            "first_name": "Guest",
            "last_name": "Shopper",
            "phone": "+12025550195",
            "street": "Main Street",
            "street_number": "1",
            "city": "Testville",
            "zipcode": "12345",
            "country_id": country.alpha_2,
            "region_id": region.alpha,
            "pay_way_id": pay_way.id,
            "terms_accepted": True,
            "shipping_kind": "home_delivery",
        },
        format="json",
        HTTP_X_CART_ID=str(cart.uuid),
    )

    assert response.status_code == status.HTTP_201_CREATED, response.data
    order = Order.objects.get(id=response.data["id"])
    assert order.user_id is None
    by_uuid = APIClient().get(
        reverse("order-retrieve-by-uuid", kwargs={"uuid": str(order.uuid)})
    )
    assert by_uuid.status_code == status.HTTP_200_OK
