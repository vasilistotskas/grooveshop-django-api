from django.db import models
from django.utils.translation import gettext_lazy as _


class Surface(models.TextChoices):
    """Where a suggestion strip is rendered.

    A surface is the unit of configuration: each has its own
    ``RecommendationSlot`` (strategy chain, weights, limit, minimum
    fill) because the right suggestions differ by where the shopper is.
    On a product page they have intent but no commitment; in the cart
    they have committed and the job is completing the order; on an
    out-of-stock page the job is rescuing a dead end.
    """

    PDP = "pdp", _("Product page")
    CART = "cart", _("Cart")
    OUT_OF_STOCK = "out_of_stock", _("Out of stock")
    EMPTY_CART = "empty_cart", _("Empty cart")
    ORDER_EMAIL = "order_email", _("Order email")


class StrategyCode(models.TextChoices):
    """The registry keys. Adding a strategy means adding a member here
    AND a ``@register_strategy`` class — the enum is what a slot's
    ``strategy_chain`` is validated against, so an unregistered code can
    never be configured."""

    CURATED = "curated", _("Merchant curated")
    VARIANT_GROUP = "variant_group", _("Same variant group")
    CATEGORY = "category", _("Same category")
    ATTRIBUTES = "attributes", _("Shared attributes, tags and brand")
    SEMANTIC = "semantic", _("Semantic similarity")
    CO_PURCHASE = "co_purchase", _("Bought together")
    CO_VIEW = "co_view", _("Viewed together")
    POPULAR = "popular", _("Popular")


class EventKind(models.TextChoices):
    """The three events the learning loop needs. ``ATTACH`` is the one
    that matters commercially — a suggested product reaching a completed
    order — and it is written server-side by order completion, never by
    the client."""

    IMPRESSION = "impression", _("Impression")
    CLICK = "click", _("Click")
    ATTACH = "attach", _("Attach")
