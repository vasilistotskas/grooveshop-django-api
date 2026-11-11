"""Decide whether a price reduction may be announced, and against what.

Pure: no ORM, no clock, no settings. ``product.pricing.service`` loads the
spells and persists the verdict; this module only holds the rule, so it
can be tested against every case in the statute without a database.

The rule (Omnibus, as transposed in Greece and Cyprus, strictest of both):

* a reduction is announced against the lowest price of the 30 days BEFORE
  it started, fixed at its start (never rolling);
* the announced percentage is computed from that prior price;
* a product on sale for less than 30 days announces nothing;
* a further drop inside an uninterrupted reduction keeps the original
  reference, but only within 60 days of the reduction's start;
* any price increase, or the reduction stopping, ends the episode, and
  the next reduction is new with a new reference.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal
from itertools import pairwise

from product.pricing.constants import (
    PROGRESSIVE_REDUCTION_WINDOW,
    REFERENCE_WINDOW,
)

ONE_HUNDRED = Decimal(100)


@dataclass(frozen=True, slots=True)
class PriceSpell:
    """A period the consumer gross unit price was constant.

    ``valid_to`` is ``None`` for the current spell.
    """

    price: Decimal
    valid_from: datetime
    valid_to: datetime | None


@dataclass(frozen=True, slots=True)
class ReductionEpisode:
    """A reduction in force, frozen at its start.

    ``prior_price`` is ``None`` when the reduction exists but nothing may
    be announced (the product has been on sale for less than 30 days).
    """

    started_at: datetime
    prior_price: Decimal | None


def announced_discount_percent(
    prior_price: Decimal | None, current_price: Decimal | None
) -> int | None:
    """Whole percent between the prior and the current price, or ``None``.

    Floored so the announcement never overstates the reduction
    (CJEU C-330/23: the percentage is computed from the prior price).
    """
    if prior_price is None or current_price is None:
        return None
    if prior_price <= 0 or current_price >= prior_price:
        return None
    percent = (prior_price - current_price) * ONE_HUNDRED / prior_price
    return int(percent.to_integral_value(rounding=ROUND_FLOOR))


def evaluate_reduction(
    *,
    spells: Sequence[PriceSpell],
    first_offered_at: datetime,
    is_reduction: bool,
    episode: ReductionEpisode | None,
    now: datetime,
) -> ReductionEpisode | None:
    """The reduction episode in force at ``now``, or ``None``.

    ``spells`` are chronological, the last one is current and began at
    or before ``now``; they must reach back far enough to cover the 30
    days before ``now`` and the episode's own start. ``first_offered_at``
    is the start of the product's very first spell, which the list may
    have been trimmed past. ``is_reduction`` says the current price is a
    markdown or an unconditional promotion. ``episode`` is the state this
    function returned last time.

    The caller records a spell with ``valid_from == now`` whenever the
    price changes, so a reduction that starts now is anchored against
    history that does not yet contain its own price.
    """
    if not is_reduction or not spells:
        return None
    if episode is not None and _chain_holds(episode, spells):
        return episode
    return _anchor(spells, first_offered_at, now)


def _chain_holds(
    episode: ReductionEpisode, spells: Sequence[PriceSpell]
) -> bool:
    """Whether every price change since the episode began is a legal drop."""
    current = spells[-1]
    if episode.prior_price is not None and current.price >= episode.prior_price:
        return False
    for previous, spell in pairwise(spells):
        if spell.valid_from <= episode.started_at:
            continue
        if spell.price > previous.price:
            return False
        if spell.valid_from - episode.started_at > PROGRESSIVE_REDUCTION_WINDOW:
            return False
    return True


def _anchor(
    spells: Sequence[PriceSpell], first_offered_at: datetime, start: datetime
) -> ReductionEpisode | None:
    """Begin a new episode at ``start`` and fix its reference."""
    if start - first_offered_at < REFERENCE_WINDOW:
        return ReductionEpisode(started_at=start, prior_price=None)
    window_start = start - REFERENCE_WINDOW
    in_window = [
        spell.price
        for spell in spells
        if spell.valid_from < start
        and (spell.valid_to is None or spell.valid_to > window_start)
    ]
    if not in_window:
        return ReductionEpisode(started_at=start, prior_price=None)
    reference = min(in_window)
    if spells[-1].price >= reference:
        # The price is not below anything it was in the last 30 days:
        # there is no reduction to announce, and a later drop starts
        # afresh instead of inheriting this one.
        return None
    return ReductionEpisode(started_at=start, prior_price=reference)
