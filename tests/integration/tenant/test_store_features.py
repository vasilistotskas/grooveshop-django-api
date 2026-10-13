"""``TenantConfig.features``: each optional feature's effective state.

One table (``tenant/features.py``) names the plan flag and the store
settings of every feature, with the permission classes that answer 404 for
it, so the answer a client reads off ``/tenant/resolve`` is the one the API
serves by.
"""

from __future__ import annotations

import pytest

from tenant.features import STORE_FEATURES, effective_features
from tenant.models import Tenant
from tenant.serializers import StoreFeaturesSerializer, TenantConfigSerializer
from tests.utils.staff import store_tenant
from tests.utils.store_settings import switch_store_settings

pytestmark = pytest.mark.django_db

EVERY_PLAN = {
    "blog_enabled": True,
    "loyalty_enabled": True,
    "promotions_enabled": True,
    "gift_cards_enabled": True,
    "b2b_enabled": True,
    "recommendations_enabled": True,
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
    "PRODUCT_SUGGESTIONS_ENABLED": True,
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
        ("PRODUCT_SUGGESTIONS_ENABLED", {"product_suggestions"}),
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
