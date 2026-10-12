"""Record what products cost, and freeze the reduction episode they are in.

The one writer of ``ProductPriceSpell`` and of the columns derived from
it (``Product.effective_price``, ``prior_price``, ``reduction_started_at``).
Every path that can move a product's public price goes through here:

* ``Product.save`` (price, markdown, VAT) — ``plan_for_save`` /
  ``record_spells``, so the derived columns ride the same UPDATE;
* a ``Vat`` rate change or deletion — ``product.tasks.sync_price_records``;
* an unconditional automatic promotion starting, ending or being edited —
  ``promotion.offer_sync.refresh_products``;
* the nightly ``product.tasks.reevaluate_price_episodes`` sweep, which
  repairs anything those missed (the promotions toggle, a release that
  saved products before this code ran).

Nothing else may write those columns, and no queryset ``update()`` may
change a price field: it would skip all of the above.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import transaction
from django.db.models import Min, Q
from django.utils import timezone
from djmoney.money import Money

from product.models.price_spell import ProductPriceSpell
from product.models.product import PRICING_DERIVED_FIELDS, Product
from product.pricing.constants import (
    PROGRESSIVE_REDUCTION_WINDOW,
    REFERENCE_WINDOW,
)
from product.pricing.evaluation import (
    PriceSpell,
    ReductionEpisode,
    evaluate_reduction,
)
from promotion.offers import load_price_reducing_promotions
from promotion.services import PromotionEngine

if TYPE_CHECKING:
    from promotion.models import Promotion

logger = logging.getLogger(__name__)

#: Product fields whose change can move the consumer price.
PRICE_FIELDS = frozenset(
    {"price", "price_currency", "discount_percent", "vat", "vat_id"}
)

#: How far back the spells reach that an evaluation reads: the 30 days a
#: new reference looks at, and the 60 days a progressive chain lasts.
_HISTORY_LOOKBACK = max(REFERENCE_WINDOW, PROGRESSIVE_REDUCTION_WINDOW)

#: Products locked and rewritten per transaction by the bulk paths.
#: Soft-deleted products are included: their records stay current, so
#: restoring one needs no re-plan.
BATCH_SIZE = 500


@dataclass(frozen=True, slots=True)
class _History:
    spells: tuple[PriceSpell, ...]
    open_spell_id: int | None
    first_offered_at: datetime | None


@dataclass(slots=True)
class PricePlan:
    """What one product's price records must become."""

    product: Product
    now: datetime
    effective_price: Decimal
    prior_price: Decimal | None
    reduction_started_at: datetime | None
    #: The current spell to close, when the price changed.
    close_spell_id: int | None
    #: The price of the spell to open; ``None`` when it did not change.
    open_price: Decimal | None

    @property
    def derived_changed(self) -> bool:
        product = self.product
        return (
            product.effective_price != self.effective_price
            or product.prior_price != self.prior_price
            or product.reduction_started_at != self.reduction_started_at
        )

    def apply_to_product(self) -> None:
        self.product.effective_price = self.effective_price
        self.product.prior_price = self.prior_price
        self.product.reduction_started_at = self.reduction_started_at


def touches_price(update_fields: Collection[str] | None) -> bool:
    """Whether a ``save(update_fields=...)`` can change the consumer price."""
    return update_fields is None or not PRICE_FIELDS.isdisjoint(update_fields)


def _load_histories(
    product_ids: Collection[int], now: datetime
) -> dict[int, _History]:
    if not product_ids:
        return {}
    rows = (
        ProductPriceSpell.objects.filter(product_id__in=product_ids)
        .filter(
            Q(valid_to__isnull=True) | Q(valid_to__gt=now - _HISTORY_LOOKBACK)
        )
        .order_by("product_id", "valid_from")
        .values("id", "product_id", "price", "valid_from", "valid_to")
    )
    spells: dict[int, list[PriceSpell]] = {}
    open_ids: dict[int, int] = {}
    for row in rows:
        spells.setdefault(row["product_id"], []).append(
            PriceSpell(row["price"], row["valid_from"], row["valid_to"])
        )
        if row["valid_to"] is None:
            open_ids[row["product_id"]] = row["id"]
    firsts = dict(
        ProductPriceSpell.objects.filter(product_id__in=product_ids)
        .order_by()
        .values_list("product_id")
        .annotate(first=Min("valid_from"))
    )
    return {
        product_id: _History(
            tuple(product_spells),
            open_ids.get(product_id),
            firsts.get(product_id),
        )
        for product_id, product_spells in spells.items()
    }


def _plan(
    product: Product,
    history: _History | None,
    promotions: Sequence[Promotion],
    now: datetime,
) -> PricePlan:
    list_gross = product.final_price
    promotion_discount = (
        PromotionEngine.single_unit_discount(
            list(promotions), product, list_gross
        )
        if promotions
        else Decimal(0)
    )
    effective = list_gross.amount - promotion_discount
    is_reduction = product.discount_percent > 0 or promotion_discount > 0

    spells = list(history.spells) if history else []
    first_offered_at = (
        history.first_offered_at
        if history and history.first_offered_at
        else now
    )
    had_history = bool(spells)
    close_spell_id: int | None = None
    open_price: Decimal | None = None
    if not spells or spells[-1].price != effective:
        if spells:
            spells[-1] = replace(spells[-1], valid_to=now)
            close_spell_id = history.open_spell_id if history else None
        spells.append(PriceSpell(effective, now, None))
        open_price = effective

    # A product with no spells yet is new to the records, so whatever
    # episode its columns carry is not its own (a duplicated product
    # copies the original's) and starts afresh.
    episode = (
        ReductionEpisode(product.reduction_started_at, product.prior_price)
        if had_history and product.reduction_started_at is not None
        else None
    )
    verdict = evaluate_reduction(
        spells=spells,
        first_offered_at=first_offered_at,
        is_reduction=is_reduction,
        episode=episode,
        now=now,
    )
    return PricePlan(
        product=product,
        now=now,
        effective_price=effective,
        prior_price=verdict.prior_price if verdict else None,
        reduction_started_at=verdict.started_at if verdict else None,
        close_spell_id=close_spell_id,
        open_price=open_price,
    )


def plan_for_save(
    product: Product, update_fields: Collection[str] | None = None
) -> PricePlan:
    """Plan a product's records before it is saved, and set its columns.

    Takes no lock of its own: a multi-product transaction must acquire
    product row locks in pk order (see ``StockManager.lock_products``), and
    a per-save lock here would be a second, unordered acquisition. Two
    concurrent saves of one product that both try to open a spell are
    refused by the one-current-spell-per-product constraint, and the loser's
    save rolls back instead of recording a wrong price. The derived columns
    are assigned to the instance, so the save that follows writes them in
    the same statement; ``record_spells`` writes the spells once it has a pk.

    With ``update_fields`` only those fields are persisted, so the plan is
    built from the stored row with just those fields taken from the
    instance: an unsaved price on the instance is not a price anyone pays.
    """
    now = timezone.now()
    history = None
    subject = product
    if product.pk is not None:
        history = _load_histories([product.pk], now).get(product.pk)
        if update_fields is not None:
            subject = (
                Product._base_manager.select_related("vat")
                .filter(pk=product.pk)
                .first()
                or product
            )
            for name in PRICE_FIELDS.intersection(update_fields):
                if name != "vat":
                    setattr(subject, name, getattr(product, name))
            if "vat" in update_fields:
                subject.vat = product.vat
    plan = _plan(subject, history, load_price_reducing_promotions(), now)
    plan.product = product
    plan.apply_to_product()
    return plan


def record_spells(plans: Iterable[PricePlan]) -> None:
    """Close the spells that ended and open the ones that began."""
    plans = [plan for plan in plans if plan.open_price is not None]
    if not plans:
        return
    now = plans[0].now
    close_ids = [p.close_spell_id for p in plans if p.close_spell_id]
    if close_ids:
        ProductPriceSpell.objects.filter(pk__in=close_ids).update(valid_to=now)
    ProductPriceSpell.objects.bulk_create(
        ProductPriceSpell(
            product_id=plan.product.pk,
            price=Money(plan.open_price, settings.DEFAULT_CURRENCY),
            valid_from=now,
        )
        for plan in plans
    )


def sync_product_prices(
    product_ids: Iterable[int], *, now: datetime | None = None
) -> set[int]:
    """Bring spells and derived columns in line with the live prices.

    For every given product: record a spell when its effective price
    differs from the current one, and re-evaluate its reduction episode.
    Idempotent — a product already in line is left untouched. Returns the
    ids of the products whose records changed.
    """
    ids = sorted(set(product_ids))
    now = now or timezone.now()
    promotions = load_price_reducing_promotions()
    changed: set[int] = set()
    for start in range(0, len(ids), BATCH_SIZE):
        chunk = ids[start : start + BATCH_SIZE]
        with transaction.atomic():
            products = list(
                Product._base_manager.select_for_update(of=("self",))
                .select_related("vat")
                .filter(pk__in=chunk)
                .order_by("pk")
            )
            histories = _load_histories([p.pk for p in products], now)
            plans = [
                _plan(product, histories.get(product.pk), promotions, now)
                for product in products
            ]
            touched = [
                plan
                for plan in plans
                if plan.open_price is not None or plan.derived_changed
            ]
            record_spells(plans)
            stale = [plan for plan in touched if plan.derived_changed]
            for plan in stale:
                plan.apply_to_product()
            Product._base_manager.bulk_update(
                [plan.product for plan in stale], list(PRICING_DERIVED_FIELDS)
            )
            changed.update(plan.product.pk for plan in touched)
    return changed
