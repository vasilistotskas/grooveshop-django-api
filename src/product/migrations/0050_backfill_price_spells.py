"""Seed price spells and reduction episodes from the product history.

``ProductPriceSpell`` (0049) is where the Omnibus prior price is read
from. Products that existed before it have no spells, so this replays
what ``HistoricalProduct`` kept (at most 365 days: the weekly
``clean_old_history`` purge) through the same evaluation function the
live code uses, so a product whose markdown started last week has its
reference immediately.

What it CANNOT reconstruct, and deliberately does not guess:

* Prices older than the oldest surviving history row. The first spell
  starts at that row, or at ``created_at`` for a product with no history
  at all, so a product unchanged for over a year has a single spell that
  old and any markdown it carries announces nothing.
* The VAT rate in force when a row was written: history keeps the VAT
  id, not the rate, so the current rate of that VAT row is applied to
  every historical price.
* Automatic promotions. ``Promotion`` has no history, and this
  migration does not load the promotion engine (a migration must not run
  live model code). A promotion already running is picked up, with its
  start recorded as that moment, by ``product.tasks.
  reevaluate_price_episodes`` — the next nightly run.
* Anything saved by pods of the previous release while this ran: the
  nightly run reconciles it.

Soft-deleted products are seeded too, so a restored one is already
current.

Idempotent: a product that already has a spell is skipped, so a replay
adds nothing. Runs once per schema through ``migrate_schemas``. The
reverse is a no-op by design: spells are a legal record.
"""

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.db import migrations
from django.utils import timezone

from product.pricing.evaluation import PriceSpell, evaluate_reduction

BATCH = 500
TWO_PLACES = Decimal("0.01")
ONE_HUNDRED = Decimal(100)
HISTORY_DELETE = "-"


def _cents(value: Decimal) -> Decimal:
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _gross(price: Decimal, discount_percent: Decimal, vat_rate: Decimal):
    """``Product.final_price`` for one set of inputs."""
    vat = _cents(price * vat_rate / ONE_HUNDRED)
    discount = _cents(price * discount_percent / ONE_HUNDRED)
    return _cents(price + vat - discount)


def backfill_price_spells(apps, schema_editor):
    Product = apps.get_model("product", "Product")
    Historical = apps.get_model("product", "HistoricalProduct")
    Spell = apps.get_model("product", "ProductPriceSpell")
    Vat = apps.get_model("vat", "Vat")

    now = timezone.now()
    vat_rates = dict(Vat.objects.values_list("id", "value"))
    already = set(Spell.objects.values_list("product_id", flat=True).distinct())

    product_ids = [
        pk
        for pk in Product.objects.order_by("pk").values_list("pk", flat=True)
        if pk not in already
    ]
    for start in range(0, len(product_ids), BATCH):
        chunk = product_ids[start : start + BATCH]
        history = defaultdict(list)
        for row in (
            Historical.objects.filter(id__in=chunk)
            .exclude(history_type=HISTORY_DELETE)
            .order_by("id", "history_date", "history_id")
            .values(
                "id", "history_date", "price", "discount_percent", "vat_id"
            )
        ):
            history[row["id"]].append(row)

        products = {
            row["pk"]: row
            for row in Product.objects.filter(pk__in=chunk).values(
                "pk",
                "created_at",
                "price",
                "discount_percent",
                "vat_id",
                "price_currency",
            )
        }
        new_spells = []
        updates = []
        for product_id in chunk:
            current = products[product_id]
            events = [
                (
                    row["history_date"],
                    _gross(
                        row["price"],
                        row["discount_percent"],
                        vat_rates.get(row["vat_id"], Decimal(0)),
                    ),
                    row["discount_percent"] > 0,
                )
                for row in history[product_id]
            ]
            current_discount = current["discount_percent"]
            current_gross = _gross(
                current["price"],
                current_discount,
                vat_rates.get(current["vat_id"], Decimal(0)),
            )
            if not events:
                events = [
                    (
                        current["created_at"],
                        current_gross,
                        current_discount > 0,
                    )
                ]
            elif events[-1][1] != current_gross:
                # The row moved without a history row (a bulk update): the
                # price on sale today is not the one history ends on.
                events.append((now, current_gross, current_discount > 0))

            spells: list[PriceSpell] = []
            episode = None
            for at, gross, is_reduction in events:
                if not spells or spells[-1].price != gross:
                    if spells:
                        spells[-1] = PriceSpell(
                            spells[-1].price, spells[-1].valid_from, at
                        )
                    spells.append(PriceSpell(gross, at, None))
                episode = evaluate_reduction(
                    spells=spells,
                    first_offered_at=spells[0].valid_from,
                    is_reduction=is_reduction,
                    episode=episode,
                    now=at,
                )

            new_spells.extend(
                Spell(
                    product_id=product_id,
                    price=spell.price,
                    price_currency=current["price_currency"],
                    valid_from=spell.valid_from,
                    valid_to=spell.valid_to,
                )
                for spell in spells
            )
            updates.append(
                Product(
                    pk=product_id,
                    effective_price=spells[-1].price,
                    prior_price=episode.prior_price if episode else None,
                    reduction_started_at=(
                        episode.started_at if episode else None
                    ),
                )
            )
        Spell.objects.bulk_create(new_spells)
        Product.objects.bulk_update(
            updates,
            ["effective_price", "prior_price", "reduction_started_at"],
        )


class Migration(migrations.Migration):
    dependencies = [
        ("product", "0049_product_price_spell_and_reduction_episode"),
        ("vat", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            backfill_price_spells, reverse_code=migrations.RunPython.noop
        ),
    ]
