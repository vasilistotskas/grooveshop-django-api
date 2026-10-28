"""The editor's areas: namespaces grouped under names a store owner knows.

The one table that maps the storefront's ids onto human areas. A text
belongs to the FIRST area that claims it; whatever nobody claims is
"Common".

A catalogue namespace is the component's file path under ``app/``
(``components/Chrome/Footer``) or ``global`` for the shared locale files
(then the key's first segment says what it is). A store's own design
lives in ``.../variants/<schema>/...``: that segment is dropped before
matching, so a design's copy of the footer is in the same area as the
shared one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _
from django_stubs_ext import StrOrPromise

GLOBAL_NAMESPACE = "global"
COMMON = "common"

_VARIANT = re.compile(r"(?:^|/)variants/[^/]+(?=/|$)")


@dataclass(frozen=True)
class Area:
    code: str
    title: StrOrPromise
    #: Prefixes of the namespace, after ``components/`` and any
    #: ``variants/<schema>`` segment are dropped.
    namespaces: tuple[str, ...] = ()
    #: First segments of the keys of the ``global`` locale files.
    global_keys: tuple[str, ...] = ()
    #: A storefront page where these texts show, if there is one.
    preview_path: str | None = None


AREAS: tuple[Area, ...] = (
    Area(
        "header",
        _("Header"),
        (
            "Chrome/Navbar",
            "Chrome/MegaMenu",
            "Chrome/MobileMenu",
            "Chrome/AccountMenu",
            "Chrome/AccountSheet",
            "Chrome/AccountShell",
            "Chrome/CheckoutHeader",
            "Chrome/AccessibilityToolbar",
            "Chrome/FundingBanner",
            "MobileBottomNav",
            "Language",
        ),
        ("menu", "a11y"),
        "/",
    ),
    Area(
        "footer",
        _("Footer"),
        ("Chrome/Footer", "Footer", "Socials", "MerchantIdentity"),
        (
            "footer",
            "facebook",
            "instagram",
            "twitter",
            "youtube",
            "tiktok",
            "pinterest",
            "reddit",
            "discord",
        ),
        "/",
    ),
    Area(
        "legal",
        _("Legal and cookies"),
        ("Cookie", "Chrome/CookieConsent", "Storefront/Legal"),
        ("cookies",),
        "/cookies-policy",
    ),
    Area(
        "assistant",
        _("Shopping assistant"),
        ("Chat", "Chrome/Assistant"),
        ("chat",),
        "/",
    ),
    Area("home", _("Home page sections"), ("PageSection",), (), "/"),
    Area(
        "product",
        _("Product pages"),
        (
            "Product",
            "Products",
            "Storefront/Product",
            "Storefront/Brand",
            "Storefront/Offers",
            "Offers",
        ),
        (
            "products",
            "filters",
            "sort",
            "ordering",
            "limited_stock",
            "out_of_stock",
            "offers",
            "promotion",
            "categories",
            "tags",
            "reviews",
        ),
        "/products",
    ),
    Area(
        "cart",
        _("Cart"),
        ("Cart", "Storefront/Cart"),
        ("cart", "cart_empty", "add_to_cart"),
        "/cart",
    ),
    Area(
        "checkout",
        _("Checkout"),
        (
            "Checkout",
            "Shipping",
            "Order",
            "Address",
            "StripeCheckout",
            "StripePayment",
            "VivaWalletCheckout",
            "Storefront/Checkout",
        ),
        (
            "shipping",
            "tracking",
            "payment_methods",
            "payment_successful",
            "payment_failed",
            "place_order",
            "order_summary",
            "delivery_address",
            "reorder",
            "stock_reserved",
            "stock_released",
        ),
        "/checkout",
    ),
    Area(
        "account",
        _("Account"),
        (
            "Account",
            "Auth",
            "User",
            "WebAuthn",
            "Storefront/Account",
            "Storefront/Auth",
            "Storefront/Newsletter",
            "layouts/",
        ),
        (
            "account",
            "account_nav",
            "auth",
            "password",
            "two_factor",
            "sessions",
            "passkey",
            "security_key",
            "login",
            "logout",
            "register",
            "subscriptions",
            "subscription_notifications",
        ),
        "/account",
    ),
    Area(
        "offers",
        _("Loyalty and gift cards"),
        ("Loyalty", "GiftCard", "Storefront/GiftCards", "Storefront/Loyalty"),
        ("loyalty", "gift_cards"),
        "/gift-cards",
    ),
    Area(
        "search",
        _("Search"),
        ("Search", "Storefront/Search"),
        ("search", "results"),
        "/search",
    ),
    Area("blog", _("Blog"), ("Blog", "Storefront/Blog"), ("blog",), "/blog"),
    Area(
        "contact",
        _("Contact and forms"),
        (
            "Contact",
            "ContactForm",
            "FeedbackForm",
            "Storefront/Contact",
            "Storefront/Feedback",
            "Form",
            "DynamicForm",
        ),
        ("form", "validation", "email", "phone", "message"),
        "/contact",
    ),
)

COMMON_AREA = Area(COMMON, _("Common"))
ALL_AREAS: tuple[Area, ...] = (*AREAS, COMMON_AREA)
_BY_CODE = {area.code: area for area in ALL_AREAS}


def area_by_code(code: str) -> Area | None:
    return _BY_CODE.get(code)


def normalise_namespace(namespace: str) -> str:
    """``components/variants/webside/Footer/X`` -> ``Footer/X``."""
    path = _VARIANT.sub("", namespace)
    return path.removeprefix("components/").lstrip("/")


def area_of(namespace: str, key: str) -> Area:
    if namespace == GLOBAL_NAMESPACE:
        head = key.split(".", 1)[0]
        return next(
            (area for area in AREAS if head in area.global_keys), COMMON_AREA
        )
    path = normalise_namespace(namespace)
    return next(
        (
            area
            for area in AREAS
            if any(path.startswith(prefix) for prefix in area.namespaces)
        ),
        COMMON_AREA,
    )


_CAMEL_BREAK = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def component_name(namespace: str) -> str:
    """The component a text belongs to, as words a store owner can read:
    ``components/Account/2Fa/AuthenticateCode`` is "Authenticate code".
    The shared locale files are "Shared text"."""
    if namespace == GLOBAL_NAMESPACE:
        return str(_("Shared text"))
    last = normalise_namespace(namespace).rsplit("/", 1)[-1]
    words = _CAMEL_BREAK.sub(" ", last).replace("-", " ").replace("_", " ")
    return words.lower().capitalize() or last
