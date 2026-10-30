"""Which optional store features the admin shows, per request.

A feature has two switches, and both must be on for shoppers to see it:
the PLAN flag on ``Tenant`` (what the store paid for, set by the
platform; not every feature has one) and the store SETTINGS (whether the
owner wants it on now). ``tenant.features.STORE_FEATURES`` names both for
every feature, the same table the API's 404 gates and
``TenantConfig.features`` read, so the admin cannot disagree with
checkout about what is on. A feature of the admin only (the retail
documents, behind ``MYDATA_ENABLED``) is declared beside it here.

Each feature is in one of three states (``Availability``): live, plan on
with the setting off, or off the plan. ``FEATURE_SURFACES`` lists the
model pages each feature owns, and the rule is one for every surface:

    a feature's admin surface is shown when the store can use the
    feature, or when it holds records. Nothing that exists is made
    unreachable; nothing empty and unusable is shown.

- A SETUP page (tiers, promotions, slots, a product's loyalty points)
  is where the owner prepares the feature. It follows the plan: shown
  with the setting off, marked "Off", but only to a role that can switch
  the setting on (``CONFIGURE_PERMISSION``); hidden off the plan.
- A RECORD page (receipts, ledgers, subscriptions) is shown while the
  feature is live, or while it holds rows. A feature off the plan that
  still holds rows stays readable and nothing more: no edit, no add.
  A record is only ever created while the feature is live.
- Tabs, inlines, fields and list filters on one object follow the same
  rule with that object's own data (``Gate``, ``FeatureInline``).

``BaseModelAdmin`` enforces it in the admin's permission methods, so a
typed URL is refused as the sidebar and the ⌘K palette (both built from
``has_module_permission``) omit the page.

State is computed once per request (``store_features``), and the one
``EXISTS`` a record check costs is made once per model, only for a
feature that is not live.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from django.apps import apps
from django.db.models import Model, Q

from core.store_settings import store_settings
from tenant.features import STORE_FEATURES, StoreFeature
from tenant.membership import tenant_plan_allows
from tenant.permissions import IsSettingEnabled

#: Who may see a switched-off feature's setup: those who can switch it on.
#: OWNER and ADMIN hold it; STAFF and the operational roles do not.
CONFIGURE_PERMISSION = "extra_settings.change_setting"

#: Whether the store sends its receipts to AADE's myDATA. This is when
#: the receipt pages are SHOWN. Whether the platform can ISSUE one is the
#: stricter ``MyDataConfig.is_ready`` (environment, FHM ground,
#: credentials): the order actions ask that, the admin pages do not.
RETAIL_DOCUMENTS = "retail_documents"


class _MyDataEnabled(IsSettingEnabled):
    """The retail documents' switch. Not a gate of any route (a server-side
    setting no client reads), so it is not in ``STORE_FEATURES``."""

    setting_key = "MYDATA_ENABLED"


FEATURES: dict[str, StoreFeature] = {
    **STORE_FEATURES,
    RETAIL_DOCUMENTS: StoreFeature(None, (_MyDataEnabled,)),
}


class Availability(StrEnum):
    LIVE = "live"
    SETTING_OFF = "setting_off"
    UNAVAILABLE = "unavailable"

    @property
    def configurable(self) -> bool:
        """The plan allows it: the store can prepare it and keep editing
        what it holds."""
        return self is not Availability.UNAVAILABLE

    @property
    def live(self) -> bool:
        return self is Availability.LIVE


class Kind(StrEnum):
    SETUP = "setup"
    RECORD = "record"


@dataclass(frozen=True)
class Surfaces:
    """The model pages (``app.Model``) a feature owns."""

    setup: tuple[str, ...] = ()
    records: tuple[str, ...] = ()


FEATURE_SURFACES: dict[str, Surfaces] = {
    "catalogue": Surfaces(
        setup=(
            "product.Product",
            "product.ProductCategory",
            "product.Brand",
            "product.Attribute",
            "product.AttributeValue",
            "product.ProductVariantGroup",
            "product.ProductImage",
            "product.ProductCategoryImage",
        )
    ),
    "cart": Surfaces(
        records=(
            "cart.Cart",
            "cart.CartItem",
            "order.Order",
            "order.OrderItem",
        )
    ),
    RETAIL_DOCUMENTS: Surfaces(
        records=(
            "order.Invoice",
            "order.RetailCredit",
            "order.InvoiceCounter",
            "order.CreditCounter",
        )
    ),
    "loyalty": Surfaces(
        setup=("loyalty.LoyaltyTier",),
        records=("loyalty.PointsTransaction",),
    ),
    "promotions": Surfaces(
        setup=("promotion.Promotion", "promotion.PromotionCode"),
        records=("promotion.PromotionRedemption",),
    ),
    "gift_cards": Surfaces(
        records=(
            "giftcard.GiftCard",
            "giftcard.GiftCardPurchase",
            "giftcard.GiftCardTransaction",
        )
    ),
    "wholesale": Surfaces(
        setup=("b2b.CustomerGroup", "b2b.PriceListItem"),
        records=("b2b.BusinessProfile",),
    ),
    "product_suggestions": Surfaces(
        setup=("recommendation.RecommendationSlot",),
        records=(
            "recommendation.RecommendationEvent",
            "recommendation.RecommendationCandidate",
        ),
    ),
    "blog": Surfaces(
        records=(
            "blog.BlogPost",
            "blog.BlogCategory",
            "blog.BlogAuthor",
            "blog.BlogTag",
        )
    ),
    "blog_comments": Surfaces(records=("blog.BlogComment",)),
    "product_reviews": Surfaces(records=("product.ProductReview",)),
    "favourites": Surfaces(records=("product.ProductFavourite",)),
    "newsletter": Surfaces(
        records=("user.UserSubscription", "user.SubscriptionTopic")
    ),
    "feedback": Surfaces(records=("contact.Feedback",)),
    # Surfaces on other models' forms only (see ``Gate`` and
    # ``FeatureInline``): they own no page of their own.
    "b2b_invoicing": Surfaces(),
    "product_alerts": Surfaces(),
    "contact_attachments": Surfaces(),
}


@dataclass(frozen=True)
class Placement:
    feature: str
    kind: Kind


def _placements() -> dict[str, Placement]:
    placements: dict[str, Placement] = {}
    for name, surfaces in FEATURE_SURFACES.items():
        for kind, labels in (
            (Kind.SETUP, surfaces.setup),
            (Kind.RECORD, surfaces.records),
        ):
            for label in labels:
                if label in placements:
                    raise ValueError(f"{label} belongs to two features")
                placements[label] = Placement(name, kind)
    return placements


#: Model label -> the feature that owns its page, and what kind of page.
PLACEMENTS: dict[str, Placement] = _placements()


@dataclass(frozen=True)
class Gate:
    """Fields and list filters of one admin that belong to a feature.

    ``fields`` are removed from the change form (a fieldset left empty
    goes too) and ``filters`` from the changelist (the exact entries of
    ``list_filter``) unless the feature shows here:

    - ``Kind.SETUP`` (the default is ``RECORD``): the owner prepares it,
      so it shows while the plan allows, to the role that can switch the
      setting on;
    - ``Kind.RECORD``: it shows while the feature is live, or when the
      object (the changelist: any row) has data for it. ``data`` says
      what that is, as a filter on the admin's model; without it, any
      row of the feature's own record pages.
    """

    feature: str
    fields: tuple[str, ...] = ()
    filters: tuple[Any, ...] = ()
    kind: Kind = Kind.RECORD
    data: Q | None = None

    def __post_init__(self) -> None:
        if self.feature not in FEATURES:
            raise ValueError(f"{self.feature} is not a feature")


@dataclass
class StoreFeatures:
    """Every feature's state for the request being served, and the
    questions the admin asks of it."""

    states: Mapping[str, Availability]
    _memo: dict[Any, bool] = field(default_factory=dict)

    @classmethod
    def read(cls) -> StoreFeatures:
        names = {
            gate.setting_key
            for feature in FEATURES.values()
            for gate in feature.settings
        }
        values = store_settings(names)
        return cls(
            {
                name: _availability(feature, values)
                for name, feature in FEATURES.items()
            }
        )

    def state(self, name: str) -> Availability:
        return self.states[name]

    def live(self, name: str) -> bool:
        return self.states[name].live

    def placement(self, label: str) -> Placement | None:
        return PLACEMENTS.get(label)

    def is_off(self, label: str) -> bool:
        """A page that is listed while its feature is not live."""
        placement = PLACEMENTS.get(label)
        return placement is not None and not self.live(placement.feature)

    # What a page, an object's surface or a model's page may do.

    def shows(
        self,
        request: Any,
        feature: str,
        *,
        kind: Kind,
        has_data: Callable[[], bool],
    ) -> bool:
        """The one rule: whether a surface of ``feature`` is shown.
        ``has_data`` answers for a record surface that is not live, and
        is only called then."""
        state = self.state(feature)
        if kind is Kind.SETUP:
            return state.configurable and (
                state.live or _can_configure(request)
            )
        return state.live or has_data()

    def shows_records(self, request: Any, feature: str) -> bool:
        """A record surface of ``feature`` that is not tied to one
        object (a list filter's choice): shown while the feature is live
        or holds any row on its own pages."""
        return self.shows(
            request,
            feature,
            kind=Kind.RECORD,
            has_data=lambda: self.holds_records(feature),
        )

    def may_modify(self, feature: str) -> bool:
        """What is shown can be changed, off the plan it is read-only."""
        return self.state(feature).configurable

    def may_create(self, feature: str, kind: Kind) -> bool:
        """A record is created only while its feature is live; a setup
        page keeps adding while it is shown."""
        return kind is Kind.SETUP or self.live(feature)

    def has_rows(self, label: str) -> bool:
        return self.remember(
            ("rows", label),
            lambda: apps.get_model(label)._default_manager.exists(),
        )

    def holds_records(self, feature: str) -> bool:
        """Any row on a page the feature owns."""
        return any(self.has_rows(label) for label in _records(feature))

    def remember(self, key: Any, answer: Callable[[], bool]) -> bool:
        """``answer()`` once per request for ``key``."""
        if key not in self._memo:
            self._memo[key] = bool(answer())
        return self._memo[key]

    def gate_shows(
        self, request: Any, model: type[Model], gate: Gate, obj: Any = None
    ) -> bool:
        """Whether ``gate``'s fields or filters show on ``model``'s
        admin, for ``obj`` (``None``: the changelist, any row)."""

        def has_data() -> bool:
            if gate.data is None:
                return self.holds_records(gate.feature)
            rows = model._default_manager.filter(gate.data)
            if obj is not None:
                rows = rows.filter(pk=obj.pk)
            return rows.exists()

        key = ("gate", model._meta.label, id(gate), getattr(obj, "pk", None))
        return self.remember(
            key,
            lambda: self.shows(
                request, gate.feature, kind=gate.kind, has_data=has_data
            ),
        )

    # Carriers are not features of a plan: a store lists the ones active.

    def inactive_carrier_apps(self) -> frozenset[str]:
        """App labels of the carriers this store does not use. Its
        sidebar lists none of their pages (a platform superuser's System
        section keeps their logs) and the ⌘K palette none of their
        screens beyond what the sidebar shows."""
        from shipping.models import ShippingProvider
        from shipping_acs.carrier import AcsCarrier
        from shipping_boxnow.carrier import BoxNowCarrier

        active = set(
            ShippingProvider.objects.filter(is_active=True).values_list(
                "code", flat=True
            )
        )
        return frozenset(
            app
            for app, code in (
                ("shipping_acs", AcsCarrier.code),
                ("shipping_boxnow", BoxNowCarrier.code),
            )
            if code not in active
        )


def _records(feature: str) -> tuple[str, ...]:
    return FEATURE_SURFACES[feature].records


def _availability(
    feature: StoreFeature, setting_values: Mapping[str, Any]
) -> Availability:
    if feature.plan is not None and not tenant_plan_allows(
        feature.plan.feature_flag
    ):
        return Availability.UNAVAILABLE
    if all(bool(setting_values[gate.setting_key]) for gate in feature.settings):
        return Availability.LIVE
    return Availability.SETTING_OFF


def _can_configure(request: Any) -> bool:
    return bool(request.user.has_perm(CONFIGURE_PERMISSION))


def store_features(request: Any) -> StoreFeatures:
    """The request's feature state, read once and kept on the request."""
    features = getattr(request, "_admin_store_features", None)
    if features is None:
        features = StoreFeatures.read()
        request._admin_store_features = features
    return features
