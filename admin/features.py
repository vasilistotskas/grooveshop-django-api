"""Which optional store features the admin should show, per request.

A plan-gated feature (loyalty, promotions, gift cards, wholesale,
recommendations) has two switches, and both must be on for shoppers to
see it: the PLAN flag on ``Tenant`` (what the store paid for, set by the
platform) and the store SETTING (whether the owner wants it on now). The
admin reads the two separately:

- plan off: the feature does not exist for this store, so every admin
  surface for it is hidden;
- plan on, setting off: its setup pages stay, marked off, so an owner
  can prepare tiers or coupons before shoppers see them, while counters
  and dashboard rows go - nothing new arrives;
- both on: live.

Each switch is read through the code that enforces it for shoppers (the
feature's own ``is_enabled()``, the permission classes' flag and setting
names), so the admin cannot disagree with checkout about what is on.
The state is computed once per request: the sidebar and the dashboard
both read it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from extra_settings.models import Setting

from b2b.services import B2BService
from giftcard.services import GiftCardService
from loyalty.services import LoyaltyService
from promotion.services import PromotionEngine
from shipping.models import ShippingProvider
from shipping_acs.carrier import AcsCarrier
from shipping_boxnow.carrier import BoxNowCarrier
from tenant.membership import tenant_plan_allows
from tenant.permissions import (
    IsB2BEnabled,
    IsBlogCommentsEnabled,
    IsBlogEnabled,
    IsFavouritesEnabled,
    IsFeedbackEnabled,
    IsGiftCardsEnabled,
    IsLoyaltyEnabled,
    IsNewsletterEnabled,
    IsProductReviewsEnabled,
    IsPromotionsEnabled,
    IsRecommendationsEnabled,
    IsRecommendationsRuntimeEnabled,
    IsSettingEnabled,
)

# Whether the store sends its receipts to AADE's myDATA.
MYDATA_SETTING = "MYDATA_ENABLED"


class Availability(StrEnum):
    LIVE = "live"
    SETTING_OFF = "setting_off"
    UNAVAILABLE = "unavailable"

    @property
    def configurable(self) -> bool:
        """Its setup pages belong in the admin."""
        return self is not Availability.UNAVAILABLE

    @property
    def live(self) -> bool:
        return self is Availability.LIVE


def _availability(plan_flag: str, enabled: bool) -> Availability:
    if not tenant_plan_allows(plan_flag):
        return Availability.UNAVAILABLE
    return Availability.LIVE if enabled else Availability.SETTING_OFF


def _setting(gate: type[IsSettingEnabled]) -> bool:
    return bool(Setting.get(gate.setting_key, default=gate.default))


@dataclass(frozen=True)
class StoreFeatures:
    loyalty: Availability
    promotions: Availability
    gift_cards: Availability
    wholesale: Availability
    recommendations: Availability
    blog: bool
    blog_comments: bool
    reviews: bool
    favourites: bool
    newsletter: bool
    feedback: bool
    mydata: bool
    acs: bool
    boxnow: bool

    @classmethod
    def read(cls) -> StoreFeatures:
        active_carriers = set(
            ShippingProvider.objects.filter(is_active=True).values_list(
                "code", flat=True
            )
        )
        blog = tenant_plan_allows(IsBlogEnabled.feature_flag)
        return cls(
            loyalty=_availability(
                IsLoyaltyEnabled.feature_flag, LoyaltyService.is_enabled()
            ),
            promotions=_availability(
                IsPromotionsEnabled.feature_flag, PromotionEngine.is_enabled()
            ),
            gift_cards=_availability(
                IsGiftCardsEnabled.feature_flag, GiftCardService.is_enabled()
            ),
            wholesale=_availability(
                IsB2BEnabled.feature_flag, B2BService.is_enabled()
            ),
            recommendations=_availability(
                IsRecommendationsEnabled.feature_flag,
                _setting(IsRecommendationsRuntimeEnabled),
            ),
            blog=blog,
            blog_comments=blog and _setting(IsBlogCommentsEnabled),
            reviews=_setting(IsProductReviewsEnabled),
            favourites=_setting(IsFavouritesEnabled),
            newsletter=_setting(IsNewsletterEnabled),
            feedback=_setting(IsFeedbackEnabled),
            mydata=bool(Setting.get(MYDATA_SETTING, default=False)),
            acs=AcsCarrier.code in active_carriers,
            boxnow=BoxNowCarrier.code in active_carriers,
        )

    def absent_models(self) -> frozenset[str]:
        """Model labels of the pages that only receive shopper input
        (reviews, feedback, favourites, newsletter, blog comments) while
        their store setting is off. Neither the sidebar nor the ⌘K
        palette (``admin.search``) offers them."""
        gated = {
            "product.ProductReview": self.reviews,
            "contact.Feedback": self.feedback,
            "product.ProductFavourite": self.favourites,
            "user.UserSubscription": self.newsletter,
            "user.SubscriptionTopic": self.newsletter,
            "blog.BlogComment": self.blog_comments,
        }
        return frozenset(label for label, on in gated.items() if not on)

    def absent_apps(self) -> frozenset[str]:
        """App labels of what this store does not have - a feature off
        its plan, a carrier it does not use. The store's sidebar lists
        none of their pages (a platform superuser's System section keeps
        its carrier logs), and the ⌘K palette (``admin.search``) none of
        their screens beyond what the sidebar shows."""
        absent = {
            app
            for app, availability in (
                ("loyalty", self.loyalty),
                ("promotion", self.promotions),
                ("giftcard", self.gift_cards),
                ("b2b", self.wholesale),
                ("recommendation", self.recommendations),
            )
            if not availability.configurable
        }
        for app, present in (
            ("blog", self.blog),
            ("shipping_acs", self.acs),
            ("shipping_boxnow", self.boxnow),
        ):
            if not present:
                absent.add(app)
        return frozenset(absent)


def store_features(request: Any) -> StoreFeatures:
    """The request's feature state, read once and kept on the request."""
    features = getattr(request, "_admin_store_features", None)
    if features is None:
        features = StoreFeatures.read()
        request._admin_store_features = features
    return features
