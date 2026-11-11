"""Record the new price of every marked-down product.

The markdown now reduces the net price and VAT is charged on the reduced
net (``Product.vat_value``), so ``final_price`` of every product with
``discount_percent > 0`` and a VAT rate dropped by the VAT on the markdown
(30.00 net, 24% VAT, 10% off: 34.20 before, 33.48 now). That is a real
price change for the shopper, so it is recorded like any other: the open
spell is closed, a new one opens at the new price, and the reduction
episode is re-evaluated by the same pure function the live code uses
(a drop inside an episode keeps its reference within 60 days; past that
it is a new reduction measured against the 30 days before).

Only products whose current spell still holds the price the OLD formula
gave are touched. One whose recorded price differs carries a promotion
(this migration does not load the promotion engine); the nightly
``product.tasks.reevaluate_price_episodes`` re-prices those, and every
other product it would have found is already current.

Idempotent: after a product is migrated its spell holds the new price, so
a replay finds nothing to do. The reverse is a no-op: spells are a legal
record.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db import migrations
from django.utils import timezone

from product.pricing.evaluation import (
    PriceSpell,
    ReductionEpisode,
    evaluate_reduction,
)

TWO_PLACES = Decimal("0.01")
HUNDRED = Decimal(100)


def _cents(value):
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _old_final(price, discount_percent, vat_rate):
    vat = _cents(price * vat_rate / HUNDRED)
    discount = _cents(price * discount_percent / HUNDRED)
    return _cents(price + vat - discount)


def _new_final(price, discount_percent, vat_rate):
    discount = _cents(price * discount_percent / HUNDRED)
    vat = _cents((price - discount) * vat_rate / HUNDRED)
    return _cents(price + vat - discount)


def reprice(apps, schema_editor):
    Product = apps.get_model("product", "Product")
    Spell = apps.get_model("product", "ProductPriceSpell")
    Vat = apps.get_model("vat", "Vat")

    now = timezone.now()
    rates = dict(Vat.objects.values_list("id", "value"))
    updates = []
    for product in Product.objects.filter(
        discount_percent__gt=0, vat_id__isnull=False
    ).only(
        "id",
        "price",
        "price_currency",
        "discount_percent",
        "vat_id",
        "reduction_started_at",
        "prior_price",
    ):
        rate = rates.get(product.vat_id)
        if rate is None:
            continue
        price = product.price.amount
        old = _old_final(price, product.discount_percent, rate)
        new = _new_final(price, product.discount_percent, rate)
        spells = list(
            Spell.objects.filter(product_id=product.pk).order_by("valid_from")
        )
        if not spells or spells[-1].valid_to is not None:
            continue
        current = spells[-1]
        if current.price.amount != old or new == old:
            continue

        Spell.objects.filter(pk=current.pk).update(valid_to=now)
        Spell.objects.create(
            product_id=product.pk,
            price=new,
            price_currency=current.price_currency,
            valid_from=now,
        )
        history = [
            PriceSpell(s.price.amount, s.valid_from, s.valid_to) for s in spells
        ]
        history[-1] = PriceSpell(old, current.valid_from, now)
        history.append(PriceSpell(new, now, None))
        episode = (
            ReductionEpisode(product.reduction_started_at, product.prior_price)
            if product.reduction_started_at is not None
            else None
        )
        verdict = evaluate_reduction(
            spells=history,
            first_offered_at=history[0].valid_from,
            is_reduction=True,
            episode=episode,
            now=now,
        )
        product.effective_price = new
        product.prior_price = verdict.prior_price if verdict else None
        product.reduction_started_at = verdict.started_at if verdict else None
        updates.append(product)
    Product.objects.bulk_update(
        updates, ["effective_price", "prior_price", "reduction_started_at"]
    )


class Migration(migrations.Migration):
    dependencies = [
        ("product", "0050_backfill_price_spells"),
        ("vat", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(reprice, reverse_code=migrations.RunPython.noop),
    ]
