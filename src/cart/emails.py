"""The cart email: its type, builder and sample.

The reminder to a customer who reserved stock and never ordered.
``emails.EmailsConfig`` imports this module at boot;
``order.tasks.send_checkout_abandonment_emails`` chooses WHEN to send.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django.utils.translation import gettext_noop

from core.utils.tenant_urls import get_tenant_frontend_url
from emails.declare import marketing_email
from emails.gates import FeatureGate
from emails.recipients import Recipient, customer_of
from emails.types import RenderEnv
from tenant.credentials import tenant_site_name

if TYPE_CHECKING:
    from cart.models import Cart

SAMPLE_CART_UUID = UUID("00000000-0000-4000-8000-000000000001")


def _lines(cart: Cart):
    """The cart's lines, read NOW, in the active language: parler fixes a
    product's language when it is loaded, and the task loaded them in the
    worker's. A sample carries its own lines."""
    if cart._state.adding:
        return vars(cart)["sample_items"]
    return list(
        cart.items.select_related("product").prefetch_related(
            "product__translations"
        )
    )


def _checkout_abandoned(cart: Cart, env: RenderEnv) -> dict[str, Any]:
    return {
        "site_name": tenant_site_name(),
        "cart": cart,
        "items": _lines(cart),
        # The CTA points at the recovery route: the storefront page there
        # re-attaches the cart to the session (including the logged-out ->
        # login -> back round-trip) and forwards to /cart with a
        # ``recovered=1`` flag so the shopper sees a welcome banner.
        "cart_url": get_tenant_frontend_url(
            f"/cart/recover/{cart.uuid}", language=env.language
        ),
        "preferences_url": get_tenant_frontend_url(
            "/account/subscriptions/", language=env.language
        ),
    }


def _owner(cart: Cart) -> list[Recipient]:
    if cart.user is None or not cart.user.email:
        return []
    return [customer_of(cart.user)]


def _sample_cart(env: RenderEnv) -> Cart:
    from cart.models import Cart, CartItem
    from product.models import Product
    from user.models import UserAccount

    lines = []
    for name, quantity in (("Wireless headphones", 1), ("USB-C cable", 2)):
        product = Product()
        for code, _label in settings.LANGUAGES:
            product.set_current_language(code)
            product.name = name
        product.set_current_language(env.language)
        lines.append(CartItem(product=product, quantity=quantity))
    cart = Cart(
        uuid=SAMPLE_CART_UUID,
        user=UserAccount(
            id=12345,
            email=env.recipient.address,
            first_name="Maria",
            language_code=env.language,
        ),
    )
    vars(cart)["sample_items"] = lines
    return cart


marketing_email(
    "cart.checkout_abandoned",
    title=_("Abandoned checkout reminder"),
    description=_(
        "Sent once to a signed-in customer who reserved stock and never "
        "placed the order."
    ),
    area="orders",
    templates="emails/cart/checkout_abandoned",
    subject=gettext_noop("Did you forget something? — {site_name}"),
    build_context=_checkout_abandoned,
    sample=_sample_cart,
    recipients=_owner,
    list_id="abandoned-cart",
    gates=(FeatureGate(feature="cart", label=_("Cart feature")),),
)
