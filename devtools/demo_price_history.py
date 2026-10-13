"""The demo store's price history: what makes its sale badges lawful.

The demo catalogue is seeded already marked down, so every product's only
price spell begins the day it was created and ``evaluate_reduction``
(rightly) refuses to announce anything: a product on sale for less than
``REFERENCE_WINDOW`` has no prior price. A sales showcase with no badges
shows nothing, and forging ``prior_price`` would be the one thing the
Omnibus records exist to prevent.

So the seed tells the truth about a believable past instead: each
marked-down product is recorded as having been offered at its full price
from before the look-back window, and marked down at a fixed point inside
the progressive window. The full-price spell is the only record written
here; the reduction's spell and the derived ``effective_price`` /
``prior_price`` / ``reduction_started_at`` columns come from
``product.pricing.service.sync_product_prices``, the same entry point the
app and the nightly sweep use.

Demo-only on purpose, and kept out of ``product/``: spells are legal
records that nothing in the platform may delete or backdate. This module
may, because it only ever touches the seed's own synthetic ``demo-``
products, on a store the seed command has already proven to be a
disposable showcase.

It stays valid as time passes without being re-run: the episode is frozen
at its start, and ``evaluate_reduction`` keeps it while no price has
changed since (the 60-day window limits further *drops*, not the age of an
unchanged reduction). If a demo product's price or markdown changes, the
next run rebuilds its history anchored on "now".
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from djmoney.money import Money

from devtools.demo_catalogue import PRODUCTS
from product.pricing.constants import (
    PROGRESSIVE_REDUCTION_WINDOW,
    REFERENCE_WINDOW,
)

#: Reductions start this many equal steps into the progressive window, one
#: step per marked-down product in turn, so the badges do not all share
#: one date.
_START_STEPS = 5
_WINDOW_PARTS = 12


def reduction_offset(position: int) -> timedelta:
    """How long before the seed a marked-down product's reduction began.

    Deterministic by position, and always inside the progressive window.
    """
    step = PROGRESSIVE_REDUCTION_WINDOW / _WINDOW_PARTS
    return step * (1 + position % _START_STEPS)


def seed_price_history() -> dict[str, int]:
    """Give every marked-down demo product an honest price history."""
    from django.utils import timezone

    from product.models import Product
    from product.models.price_spell import ProductPriceSpell
    from product.pricing import service

    now = timezone.now()
    marked_down = [row for row in PRODUCTS if Decimal(row.discount) > 0]
    products = {
        product.slug: product
        for product in Product.objects.select_related("vat").filter(
            slug__in=[row.slug for row in marked_down], active=True
        )
    }
    report: dict[str, int] = {}
    for position, row in enumerate(marked_down):
        product = products.get(row.slug)
        if product is None:
            continue
        full_price = Product(
            price=product.price,
            vat=product.vat,
            discount_percent=Decimal(0),
        ).final_price
        if _is_seeded(product, full_price.amount):
            report["unchanged"] = report.get("unchanged", 0) + 1
            continue

        started = now - reduction_offset(position)
        with transaction.atomic():
            ProductPriceSpell.objects.filter(product=product).delete()
            ProductPriceSpell.objects.create(
                product=product,
                price=full_price,
                valid_from=started - REFERENCE_WINDOW - timedelta(days=1),
                valid_to=started,
            )
            product.effective_price = None
            product.prior_price = None
            product.reduction_started_at = None
            Product._base_manager.bulk_update(
                [product],
                ["effective_price", "prior_price", "reduction_started_at"],
            )
            # Evaluated AS OF the reduction's start, which is when it
            # began: the service records the reduction spell there and
            # anchors the episode against the full-price spell before it.
            service.sync_product_prices([product.pk], now=started)
        report["rebuilt"] = report.get("rebuilt", 0) + 1
    return report


def _is_seeded(product, full_price: Decimal) -> bool:
    """Whether the history already is the one this seed would write."""
    from product.models.price_spell import ProductPriceSpell

    spells = list(
        ProductPriceSpell.objects.filter(product=product).order_by("valid_from")
    )
    if len(spells) != 2:
        return False
    full, current = spells
    return (
        full.price == Money(full_price, full.price.currency)
        and full.valid_to == current.valid_from
        and current.valid_to is None
        and product.prior_price == full_price
        and product.reduction_started_at == current.valid_from
        and product.effective_price == current.price.amount
    )
