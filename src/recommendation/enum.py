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


# Surfaces answered without a seed product: the shopper has nothing in
# front of them to be "like", so only store-wide strategies can answer
# (``RecommendationStrategy.store_wide``). Every other surface requires
# at least one seed.
SEEDLESS_SURFACES = frozenset({Surface.EMPTY_CART})


class StrategyCode(models.TextChoices):
    """The registry keys. Adding a strategy means adding a member here
    AND a ``@register_strategy`` class in the same change — the enum is
    what a slot's ``strategy_chain`` is validated against, and a test
    pins that every member is registered, so the admin can never offer
    a strategy that does nothing."""

    CURATED = "curated", _("Merchant curated")
    VARIANT_GROUP = "variant_group", _("Same variant group")
    CATEGORY = "category", _("Same category")
    ATTRIBUTES = "attributes", _("Shared attributes, tags and brand")
    CO_PURCHASE = "co_purchase", _("Bought together")
    POPULAR = "popular", _("Popular")


class EventKind(models.TextChoices):
    """The three events the learning loop needs. ``ATTACH`` is the one
    that matters commercially — a suggested product reaching a completed
    order — and it is written server-side by order completion, never by
    the client."""

    IMPRESSION = "impression", _("Impression")
    CLICK = "click", _("Click")
    ATTACH = "attach", _("Attach")


class AttachMatch(models.TextChoices):
    """How an order line was tied back to the impression that showed
    the product, in precedence order. ``impression`` is exact: the
    storefront carried the impression id on add-to-cart and it rode the
    cart line onto the order line — the only match that needs no cart
    or customer identity at the time the strip was shown, which a guest
    on their first visit has neither of. The two windows are recorded
    too so they can be compared on data rather than chosen blind:
    ``cart`` is the same basket within
    ``RECOMMENDATION_ATTACH_CART_WINDOW_HOURS`` (the one shopping journey,
    guest or not); ``user`` is the same signed-in customer within
    ``RECOMMENDATION_ATTACH_USER_WINDOW_DAYS`` (saw it on the phone,
    bought on the laptop)."""

    IMPRESSION = "impression", _("Carried from add to cart")
    CART = "cart", _("Same cart")
    USER = "user", _("Same customer")
