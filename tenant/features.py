"""Whether each optional store feature is on for a store, in one place.

A feature is on when its plan flag on ``Tenant`` (if it has one) and every
store setting it needs are on. The table names the same permission classes
that answer 404 for the feature, so what ``TenantConfig.features`` tells a
client can never disagree with what the API serves, and no client has to
combine the two tiers, or copy the settings' defaults, itself.

Store preferences ride beside them in ``TenantConfig.preferences``: a
store's switches that the API does not refuse on, so a client still learns
each one's state from the tenant payload instead of a settings read that
can fail (``docs/store-features.md``).
"""

from __future__ import annotations

from dataclasses import dataclass

from django_tenants.utils import get_public_schema_name, schema_context

from tenant.permissions import (
    IsAccountReviewsEnabled,
    IsAgentCommerceEnabled,
    IsAgentCommerceRuntimeEnabled,
    IsB2BEnabled,
    IsB2BInvoicingEnabled,
    IsB2BWholesaleEnabled,
    IsBlogCommentsEnabled,
    IsBlogEnabled,
    IsCartEnabled,
    IsCatalogueEnabled,
    IsChatWidgetEnabled,
    IsContactAttachmentsEnabled,
    IsFavouritesEnabled,
    IsFeedbackEnabled,
    IsGiftCardsEnabled,
    IsGiftCardsRuntimeEnabled,
    IsLoyaltyEnabled,
    IsLoyaltyRuntimeEnabled,
    IsNewsletterEnabled,
    IsPriceHistoryEnabled,
    IsProductAlertsEnabled,
    IsProductReviewsEnabled,
    IsPromotionsEnabled,
    IsPromotionsRuntimeEnabled,
    IsRecommendationsEnabled,
    IsRecommendationsRuntimeEnabled,
    IsSettingEnabled,
    IsTenantFeatureEnabled,
)


@dataclass(frozen=True)
class StoreFeature:
    """A feature's plan flag (``None`` when the plan does not gate it) and
    the store settings it needs, all of them on."""

    plan: type[IsTenantFeatureEnabled] | None
    settings: tuple[type[IsSettingEnabled], ...] = ()


STORE_FEATURES: dict[str, StoreFeature] = {
    "blog": StoreFeature(IsBlogEnabled),
    "blog_comments": StoreFeature(IsBlogEnabled, (IsBlogCommentsEnabled,)),
    "product_reviews": StoreFeature(None, (IsProductReviewsEnabled,)),
    "account_reviews": StoreFeature(
        None, (IsProductReviewsEnabled, IsAccountReviewsEnabled)
    ),
    # Saved products, alerts, gift cards and suggestions only exist with the
    # catalogue they point at, so each lists it first.
    "favourites": StoreFeature(None, (IsCatalogueEnabled, IsFavouritesEnabled)),
    "newsletter": StoreFeature(None, (IsNewsletterEnabled,)),
    "feedback": StoreFeature(None, (IsFeedbackEnabled,)),
    "loyalty": StoreFeature(IsLoyaltyEnabled, (IsLoyaltyRuntimeEnabled,)),
    "promotions": StoreFeature(
        IsPromotionsEnabled, (IsPromotionsRuntimeEnabled,)
    ),
    "gift_cards": StoreFeature(
        IsGiftCardsEnabled, (IsCatalogueEnabled, IsGiftCardsRuntimeEnabled)
    ),
    "wholesale": StoreFeature(IsB2BEnabled, (IsB2BWholesaleEnabled,)),
    "product_suggestions": StoreFeature(
        IsRecommendationsEnabled,
        (IsCatalogueEnabled, IsRecommendationsRuntimeEnabled),
    ),
    "b2b_invoicing": StoreFeature(None, (IsB2BInvoicingEnabled,)),
    "product_alerts": StoreFeature(
        None, (IsCatalogueEnabled, IsProductAlertsEnabled)
    ),
    "price_history": StoreFeature(None, (IsPriceHistoryEnabled,)),
    "contact_attachments": StoreFeature(None, (IsContactAttachmentsEnabled,)),
    # The widget talks to the agent gateway's chat, which only serves a
    # store whose agent commerce is on at both tiers.
    "chat_widget": StoreFeature(
        IsAgentCommerceEnabled,
        (IsAgentCommerceRuntimeEnabled, IsChatWidgetEnabled),
    ),
    "catalogue": StoreFeature(None, (IsCatalogueEnabled,)),
    # Nothing can be put in a cart from a catalogue that is not served.
    "cart": StoreFeature(None, (IsCatalogueEnabled, IsCartEnabled)),
}


#: Preference name (its key in ``TenantConfig.preferences``) → the store
#: setting that holds it. Class (b) presentation preferences.
STORE_PREFERENCES: dict[str, str] = {
    "mobile_bottom_nav": "MOBILE_BOTTOM_NAV_ENABLED",
    "sticky_add_to_cart": "STICKY_ADD_TO_CART_ENABLED",
    "recently_viewed": "RECENTLY_VIEWED_ENABLED",
    "demo_account": "DEMO_ACCOUNT_ENABLED",
}


def feature_setting_gates() -> tuple[type[IsSettingEnabled], ...]:
    """Every store-setting gate some feature depends on, once each."""
    return tuple(
        dict.fromkeys(
            gate
            for feature in STORE_FEATURES.values()
            for gate in feature.settings
        )
    )


def feature_plan_flags() -> tuple[str, ...]:
    """Every plan flag on ``Tenant`` some feature depends on, once each."""
    return tuple(
        dict.fromkeys(
            feature.plan.feature_flag
            for feature in STORE_FEATURES.values()
            if feature.plan is not None
        )
    )


def feature_setting_keys() -> frozenset[str]:
    """Every store setting some feature depends on."""
    return frozenset(gate.setting_key for gate in feature_setting_gates())


def preference_setting_keys() -> frozenset[str]:
    """Every store setting behind a preference."""
    return frozenset(STORE_PREFERENCES.values())


def effective_preferences(tenant) -> dict[str, bool]:
    """Every preference's state for ``tenant``, read in its own schema. The
    platform is not a store, so every preference is off there."""
    if tenant.schema_name == get_public_schema_name():
        return dict.fromkeys(STORE_PREFERENCES, False)

    from core.store_settings import store_settings

    with schema_context(tenant.schema_name):
        values = store_settings(STORE_PREFERENCES.values())
    return {name: bool(values[key]) for name, key in STORE_PREFERENCES.items()}


def effective_features(tenant) -> dict[str, bool]:
    """Every feature's state for ``tenant``: its plan flag read off the row,
    its settings read in its own schema. The platform is not a store, so
    every feature is off there."""
    if tenant.schema_name == get_public_schema_name():
        return dict.fromkeys(STORE_FEATURES, False)

    from core.store_settings import store_setting

    with schema_context(tenant.schema_name):
        setting_on = {
            gate: bool(store_setting(gate.setting_key))
            for feature in STORE_FEATURES.values()
            for gate in feature.settings
        }
    return {
        name: (
            feature.plan is None
            or bool(getattr(tenant, feature.plan.feature_flag))
        )
        and all(setting_on[gate] for gate in feature.settings)
        for name, feature in STORE_FEATURES.items()
    }


def feature_on(name: str) -> bool:
    """Whether ``name`` is on for the store serving this request.

    The same decision the feature's permission classes make, so a
    serializer that withholds a feature's data answers exactly as the
    feature's endpoints do: the plan flag of the current tenant (the
    platform host has none and is never gated) and every setting it
    needs, read in the active schema.
    """
    from core.store_settings import store_setting
    from tenant.membership import get_current_tenant

    feature = STORE_FEATURES[name]
    tenant = get_current_tenant()
    return not (
        feature.plan is not None
        and tenant is not None
        and not getattr(tenant, feature.plan.feature_flag, True)
    ) and all(
        bool(store_setting(gate.setting_key)) for gate in feature.settings
    )
