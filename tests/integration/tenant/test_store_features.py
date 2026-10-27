"""``TenantConfig.features``: each optional feature's effective state.

One table (``tenant/features.py``) names the plan flag and the store
settings of every feature, with the permission classes that answer 404 for
it, so the answer a client reads off ``/tenant/resolve`` is the one the API
serves by.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from product.enum.review import RateEnum
from tenant.features import STORE_FEATURES, effective_features
from tenant.models import Tenant
from tenant.serializers import StoreFeaturesSerializer, TenantConfigSerializer
from tests.utils.staff import store_tenant
from tests.utils.store_settings import switch_store_settings
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

EVERY_PLAN = {
    "blog_enabled": True,
    "loyalty_enabled": True,
    "promotions_enabled": True,
    "gift_cards_enabled": True,
    "b2b_enabled": True,
    "recommendations_enabled": True,
    "agent_commerce_enabled": True,
}
EVERY_SWITCH = {
    "BLOG_COMMENTS_ENABLED": True,
    "PRODUCT_REVIEWS_ENABLED": True,
    "ACCOUNT_REVIEWS_ENABLED": True,
    "FAVOURITES_ENABLED": True,
    "NEWSLETTER_ENABLED": True,
    "FEEDBACK_ENABLED": True,
    "LOYALTY_ENABLED": True,
    "PROMOTIONS_ENABLED": True,
    "GIFT_CARDS_ENABLED": True,
    "B2B_WHOLESALE_ENABLED": True,
    "B2B_INVOICING_ENABLED": True,
    "PRODUCT_SUGGESTIONS_ENABLED": True,
    "PRODUCT_ALERTS_ENABLED": True,
    "PRICE_HISTORY_ENABLED": True,
    "CONTACT_ATTACHMENTS_ENABLED": True,
    "AGENT_COMMERCE_ENABLED": True,
    "CHAT_WIDGET_ENABLED": True,
    "CATALOGUE_ENABLED": True,
    "CART_ENABLED": True,
}


def _store(**plan) -> Tenant:
    return Tenant(schema_name="acme", name="Acme", **{**EVERY_PLAN, **plan})


def test_the_api_names_every_feature_of_the_table():
    assert set(StoreFeaturesSerializer().fields) == set(STORE_FEATURES)


def test_everything_on_reads_on():
    switch_store_settings(**EVERY_SWITCH)

    assert effective_features(_store()) == dict.fromkeys(STORE_FEATURES, True)


@pytest.mark.parametrize(
    ("plan", "feature"),
    [
        ("blog_enabled", "blog"),
        ("blog_enabled", "blog_comments"),
        ("loyalty_enabled", "loyalty"),
        ("promotions_enabled", "promotions"),
        ("gift_cards_enabled", "gift_cards"),
        ("b2b_enabled", "wholesale"),
        ("recommendations_enabled", "product_suggestions"),
        ("agent_commerce_enabled", "chat_widget"),
    ],
)
def test_a_plan_without_the_feature_turns_it_off_whatever_the_store_says(
    plan, feature
):
    switch_store_settings(**EVERY_SWITCH)

    features = effective_features(_store(**{plan: False}))

    assert features[feature] is False
    assert sum(not on for on in features.values()) == (
        2 if plan == "blog_enabled" else 1
    )


@pytest.mark.parametrize(
    ("switch", "off"),
    [
        ("BLOG_COMMENTS_ENABLED", {"blog_comments"}),
        ("PRODUCT_REVIEWS_ENABLED", {"product_reviews", "account_reviews"}),
        ("ACCOUNT_REVIEWS_ENABLED", {"account_reviews"}),
        ("FAVOURITES_ENABLED", {"favourites"}),
        ("NEWSLETTER_ENABLED", {"newsletter"}),
        ("FEEDBACK_ENABLED", {"feedback"}),
        ("LOYALTY_ENABLED", {"loyalty"}),
        ("PROMOTIONS_ENABLED", {"promotions"}),
        ("GIFT_CARDS_ENABLED", {"gift_cards"}),
        ("B2B_WHOLESALE_ENABLED", {"wholesale"}),
        ("B2B_INVOICING_ENABLED", {"b2b_invoicing"}),
        ("PRODUCT_SUGGESTIONS_ENABLED", {"product_suggestions"}),
        ("PRODUCT_ALERTS_ENABLED", {"product_alerts"}),
        ("PRICE_HISTORY_ENABLED", {"price_history"}),
        ("CONTACT_ATTACHMENTS_ENABLED", {"contact_attachments"}),
        ("AGENT_COMMERCE_ENABLED", {"chat_widget"}),
        ("CHAT_WIDGET_ENABLED", {"chat_widget"}),
        ("CART_ENABLED", {"cart"}),
        (
            "CATALOGUE_ENABLED",
            {
                "catalogue",
                "cart",
                "favourites",
                "gift_cards",
                "product_alerts",
                "product_suggestions",
            },
        ),
    ],
)
def test_a_store_switch_turns_off_exactly_what_needs_it(switch, off):
    switch_store_settings(**{**EVERY_SWITCH, switch: False})

    features = effective_features(_store())

    assert {name for name, on in features.items() if not on} == off


def test_the_platform_is_not_a_store():
    switch_store_settings(**EVERY_SWITCH)

    public = Tenant(schema_name="public", name="Platform", **EVERY_PLAN)

    assert effective_features(public) == dict.fromkeys(STORE_FEATURES, False)


def test_resolve_serves_them_under_features():
    switch_store_settings(**{**EVERY_SWITCH, "PRODUCT_REVIEWS_ENABLED": False})

    tenant = store_tenant(
        "feature_store", **{**EVERY_PLAN, "loyalty_enabled": False}
    )

    data = TenantConfigSerializer(tenant).data

    assert data["features"]["loyalty"] is False
    assert data["features"]["product_reviews"] is False
    assert data["features"]["blog"] is True


# Every feature whose whole API surface is one switch, with a route that
# feature's permission class guards: (feature, route, method, signed in).
SERVED_BY = [
    ("product_reviews", "product-review-list", "get", False),
    ("favourites", "product-favourite-list", "get", True),
    ("blog_comments", "blog-comment-list", "get", False),
    ("newsletter", "user-subscription-topic-list", "get", True),
    ("feedback", "feedback", "post", False),
    ("product_alerts", "product-alert-list", "get", True),
    ("contact_attachments", "contact-attachment", "post", False),
    ("catalogue", "product-list", "get", False),
    ("cart", "cart-item-list", "get", False),
]


@pytest.mark.parametrize(
    ("feature", "route", "method", "signed_in"),
    SERVED_BY,
    ids=[feature for feature, *_ in SERVED_BY],
)
@pytest.mark.parametrize("on", [True, False])
def test_the_state_a_client_reads_is_what_the_api_serves(
    feature, route, method, signed_in, on
):
    switch = STORE_FEATURES[feature].settings[-1].setting_key
    switch_store_settings(**{**EVERY_SWITCH, switch: on})
    client = APIClient()
    if signed_in:
        client.force_authenticate(user=UserAccountFactory())

    response = getattr(client, method)(reverse(route))

    assert effective_features(_store())[feature] is on
    assert (response.status_code != status.HTTP_404_NOT_FOUND) is on


def test_chat_widget_needs_agent_commerce_at_both_tiers():
    switch_store_settings(**EVERY_SWITCH)
    assert effective_features(_store())["chat_widget"] is True

    switch_store_settings(AGENT_COMMERCE_ENABLED=False)
    assert effective_features(_store())["chat_widget"] is False

    switch_store_settings(AGENT_COMMERCE_ENABLED=True)
    store = _store(agent_commerce_enabled=False)
    assert effective_features(store)["chat_widget"] is False


@pytest.mark.parametrize("on", [True, False])
def test_hidden_reviews_hide_the_star_average_and_count(on):
    from product.factories.product import ProductFactory
    from product.factories.review import ProductReviewFactory
    from product.serializers.product import ProductRetrieveSerializer

    switch_store_settings(**{**EVERY_SWITCH, "PRODUCT_REVIEWS_ENABLED": on})
    product = ProductFactory(num_images=0, num_reviews=0)
    ProductReviewFactory(product=product, rate=4, status="TRUE")

    data = ProductRetrieveSerializer(product).data

    assert (data["review_count"] > 0) is on
    assert (data["review_average"] > 0) is on
    assert any(row["count"] for row in data["rating_distribution"]) is on
    assert len(data["rating_distribution"]) == len(RateEnum.values)


@pytest.mark.parametrize("on", [True, False])
def test_hidden_reviews_hide_them_from_search_hits_too(on):
    from product.factories.product import ProductFactory
    from product.factories.review import ProductReviewFactory
    from search.serializers import ProductTranslationSerializer

    switch_store_settings(**{**EVERY_SWITCH, "PRODUCT_REVIEWS_ENABLED": on})
    product = ProductFactory(num_images=0, num_reviews=0)
    ProductReviewFactory(product=product, rate=4, status="TRUE")
    hit = product.translations.first()
    serializer = ProductTranslationSerializer()

    assert (serializer.get_review_count(hit) > 0) is on
    assert (serializer.get_review_average(hit) is not None) is on


# What the catalogue switch refuses: the public catalogue routes, and the
# routes of every feature that only exists with it. (route, method,
# signed in)
CATALOGUE_ROUTES = [
    ("product-list", "get", False),
    ("product-category-list", "get", False),
    ("product-category-all", "get", False),
    ("product-category-image-list", "get", False),
    ("product-image-list", "get", False),
    ("product-brand-list", "get", False),
    ("product-attribute-list", "get", False),
    ("product-attribute-value-list", "get", False),
    ("search-product", "get", False),
    ("search-trending", "get", False),
    ("product-favourite-list", "get", True),
    ("product-alert-list", "get", True),
    ("recommendation-list", "get", False),
    ("giftcard:giftcard-mine", "get", True),
    ("giftcard:giftcard-check", "post", False),
]

# What the cart switch refuses: every shopper-facing cart action and the
# creation of an order.
CART_ROUTES = [
    ("cart-detail", "get", False),
    ("cart-reserve-stock", "post", False),
    ("cart-release-reservations", "post", False),
    ("cart-create-payment-intent", "post", False),
    ("cart-coupons", "get", False),
    ("cart-agent-token", "post", True),
    ("cart-item-list", "get", False),
    ("order-list", "post", False),
]


def _call(route, method, signed_in):
    client = APIClient()
    if signed_in:
        client.force_authenticate(user=UserAccountFactory())
    return getattr(client, method)(reverse(route))


@pytest.mark.parametrize(
    ("route", "method", "signed_in"),
    CATALOGUE_ROUTES,
    ids=[route.split(":")[-1] for route, *_ in CATALOGUE_ROUTES],
)
@pytest.mark.parametrize("on", [True, False])
def test_the_catalogue_switch_refuses_what_needs_the_catalogue(
    route, method, signed_in, on
):
    switch_store_settings(**{**EVERY_SWITCH, "CATALOGUE_ENABLED": on})

    response = _call(route, method, signed_in)

    assert (response.status_code != status.HTTP_404_NOT_FOUND) is on


@pytest.mark.parametrize(
    ("route", "method", "signed_in"),
    CART_ROUTES,
    ids=[route for route, *_ in CART_ROUTES],
)
@pytest.mark.parametrize("switch", ["CART_ENABLED", "CATALOGUE_ENABLED"])
@pytest.mark.parametrize("on", [True, False])
def test_a_store_without_a_cart_refuses_the_cart(
    route, method, signed_in, switch, on
):
    switch_store_settings(**{**EVERY_SWITCH, switch: on})

    response = _call(route, method, signed_in)

    assert (response.status_code != status.HTTP_404_NOT_FOUND) is on


@pytest.mark.parametrize("on", [True, False])
def test_the_catalogue_switch_refuses_a_product_and_its_actions(on):
    from product.factories.product import ProductFactory

    switch_store_settings(**{**EVERY_SWITCH, "CATALOGUE_ENABLED": on})
    product = ProductFactory(num_images=0, num_reviews=0)
    client = APIClient()

    for route in (
        "product-detail",
        "product-images",
        "product-tags",
        "product-variants",
        "product-reviews",
        "product-price-history",
    ):
        response = client.get(reverse(route, args=[product.pk]))
        assert (response.status_code != status.HTTP_404_NOT_FOUND) is on, route

    response = client.post(
        reverse("product-update-view-count", args=[product.pk])
    )
    assert (response.status_code != status.HTTP_404_NOT_FOUND) is on


def test_the_trending_searches_of_the_blog_outlive_the_catalogue():
    switch_store_settings(**{**EVERY_SWITCH, "CATALOGUE_ENABLED": False})
    client = APIClient()

    blog = client.get(reverse("search-trending"), {"content_type": "blog"})
    product = client.get(reverse("search-trending"))

    assert blog.status_code == status.HTTP_200_OK
    assert product.status_code == status.HTTP_404_NOT_FOUND


@pytest.fixture
def staff_api():
    from tenant.models import TenantMembershipRole
    from tests.utils.staff import (
        bind_store_tenant,
        store_staff,
        unbind_store_tenant,
    )

    tenant = store_tenant("features_staff")
    previous = bind_store_tenant(tenant)
    try:
        client = APIClient()
        client.force_authenticate(
            user=store_staff(tenant, role=TenantMembershipRole.OWNER)
        )
        yield client
    finally:
        unbind_store_tenant(previous)


def test_the_staff_routes_outlive_the_catalogue_and_the_cart(staff_api):
    switch_store_settings(
        **{**EVERY_SWITCH, "CATALOGUE_ENABLED": False, "CART_ENABLED": False}
    )

    # The staff listing of every cart.
    assert staff_api.get(reverse("cart-list")).status_code == status.HTTP_200_OK
    # Managing the catalogue: validation answers, the route is there.
    for route in (
        "product-list",
        "product-category-list",
        "product-attribute-list",
        "product-attribute-value-list",
        "product-image-list",
        "product-category-image-list",
    ):
        response = staff_api.post(reverse(route), {}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST, route


def test_the_orders_a_store_already_has_outlive_the_cart():
    switch_store_settings(
        **{**EVERY_SWITCH, "CATALOGUE_ENABLED": False, "CART_ENABLED": False}
    )
    client = APIClient()
    client.force_authenticate(user=UserAccountFactory())

    assert client.get(reverse("order-list")).status_code == status.HTTP_200_OK
    assert (
        client.get(reverse("order-my-orders")).status_code == status.HTTP_200_OK
    )
