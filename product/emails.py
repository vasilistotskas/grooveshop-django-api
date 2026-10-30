"""The product emails: their types, builders and samples.

The restock and price-drop alerts go to a customer who asked for them
(marketing: they can decline, and each carries the one-click unsubscribe);
the low-stock alert goes to the store's own staff. ``emails.EmailsConfig``
imports this module at boot. What stays with the tasks
(``product.tasks``) is choosing WHEN to send: which alerts fired, the
claim on the products a low-stock run reports, and the bookkeeping after.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django.utils.translation import gettext_noop
from djmoney.money import Money

from core.utils.tenant_urls import get_tenant_frontend_url
from emails.declare import marketing_email, staff_email
from emails.gates import FeatureGate
from emails.recipients import Recipient
from emails.types import RenderEnv
from tenant.credentials import tenant_site_name

if TYPE_CHECKING:
    from product.models import Product
    from product.models.alert import ProductAlert

TEMPLATE_DIR = "emails/product_alerts"

SAMPLE_PRODUCT_ID = 12345


@dataclass(frozen=True, slots=True)
class PriceDrop:
    """What a price-drop email is about: the alert and the price that
    reached its target."""

    alert: ProductAlert
    new_price: float


@dataclass(frozen=True, slots=True)
class LowStock:
    """What a low-stock email is about: the products it reports."""

    products: Sequence[Product]


def _product_name(product: Product, language: str) -> str:
    """The product's name in *language*. parler fixes a model's language
    when it is loaded, so the language is stated here, whatever the task
    loaded the product in."""
    product.set_current_language(language)
    return (
        product.safe_translation_getter("name", any_language=True)
        or f"Product {product.slug or product.id}"
    )


def _alert_context(alert: ProductAlert, language: str) -> dict[str, Any]:
    product = alert.product
    name = _product_name(product, language)
    return {
        "site": tenant_site_name(),
        "name": name,
        "product_name": name,
        "product_url": get_tenant_frontend_url(
            product.storefront_path, language=language
        ),
    }


def _restock(alert: ProductAlert, env: RenderEnv) -> dict[str, Any]:
    return _alert_context(alert, env.language)


def _price_drop(drop: PriceDrop, env: RenderEnv) -> dict[str, Any]:
    return {
        **_alert_context(drop.alert, env.language),
        "new_price": drop.new_price,
        "target_price": str(drop.alert.target_price.amount),
    }


def _low_stock(report: LowStock, env: RenderEnv) -> dict[str, Any]:
    rows = []
    for product in report.products:
        product.set_current_language(env.language)
        rows.append(
            {
                "name": product.safe_translation_getter(
                    "name", any_language=True
                )
                or f"Product #{product.id}",
                "sku": getattr(product, "sku", "") or "",
                "stock": product.stock,
                "low_stock_threshold": product.low_stock_threshold,
            }
        )
    return {
        "site": tenant_site_name(),
        "n": len(rows),
        "products": rows,
    }


def _recipient_of(alert: ProductAlert) -> list[Recipient]:
    """The alert's own address, else its account's, in the language the
    alert was made in; none when it has neither."""
    address = alert.email or (alert.user.email if alert.user_id else "")
    if not address:
        return []
    return [Recipient(address=address, language=alert.email_language)]


def _sample_product(env: RenderEnv, name: str, **fields: Any) -> Product:
    from product.models import Product

    product = Product(id=SAMPLE_PRODUCT_ID, slug="sample-product", **fields)
    for code, _label in settings.LANGUAGES:
        product.set_current_language(code)
        product.name = name
    product.set_current_language(env.language)
    return product


def _sample_alert(env: RenderEnv, kind: str) -> ProductAlert:
    from product.models.alert import ProductAlert

    return ProductAlert(
        kind=kind,
        product=_sample_product(env, "Wireless headphones"),
        email=env.recipient.address,
        language_code=env.language,
        target_price=Money(Decimal("30.00"), "EUR"),
    )


def _sample_low_stock(env: RenderEnv) -> LowStock:
    return LowStock(
        products=[
            _sample_product(
                env,
                name,
                sku=sku,
                stock=stock,
                low_stock_threshold=5,
            )
            for name, sku, stock in (
                ("Wireless headphones", "SKU-100", 2),
                ("USB-C cable", "SKU-200", 0),
            )
        ]
    )


ALERTS_GATE = FeatureGate(
    feature="product_alerts", label=_("Product alerts feature")
)

marketing_email(
    "product.restock_alert",
    title=_("Back in stock"),
    description=_(
        "Sent once to a customer who asked to hear when a product is "
        "available again."
    ),
    area="products",
    templates=f"{TEMPLATE_DIR}/restock_alert",
    subject=gettext_noop("[{site}] {name} is back in stock"),
    build_context=_restock,
    sample=lambda env: _sample_alert(env, "restock"),
    recipients=_recipient_of,
    list_id="product-restock-alerts",
    gates=(ALERTS_GATE,),
)
marketing_email(
    "product.price_drop_alert",
    title=_("Price drop"),
    description=_(
        "Sent once to a customer who asked to hear when a product reaches "
        "their target price."
    ),
    area="products",
    templates=f"{TEMPLATE_DIR}/price_drop_alert",
    subject=gettext_noop("[{site}] Price drop: {name} is now at your target"),
    build_context=_price_drop,
    sample=lambda env: PriceDrop(
        alert=_sample_alert(env, "price_drop"), new_price=25.5
    ),
    recipients=lambda drop: _recipient_of(drop.alert),
    list_id="product-price-drop-alerts",
    gates=(ALERTS_GATE,),
)

staff_email(
    "team.low_stock",
    title=_("Low stock alert"),
    description=_(
        "Tells the store's staff which products have run down to their "
        "low-stock threshold."
    ),
    templates=f"{TEMPLATE_DIR}/low_stock_alert",
    subject=gettext_noop("[{site}] Low stock alert — {n} product(s)"),
    build_context=_low_stock,
    sample=_sample_low_stock,
    gates=(FeatureGate(feature="catalogue", label=_("Catalogue feature")),),
)
