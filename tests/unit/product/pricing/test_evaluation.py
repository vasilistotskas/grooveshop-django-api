"""The EU Omnibus rule, case by case.

``evaluate_reduction`` is pure, so every clause of the statute is
asserted against hand-built spells with no database. Days are counted
from ``NOW``: ``day(-20)`` is twenty days ago.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from product.pricing.evaluation import (
    PriceSpell,
    ReductionEpisode,
    announced_discount_percent,
    evaluate_reduction,
)

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def day(offset: int) -> datetime:
    return NOW + timedelta(days=offset)


def spell(price: str, start: int, end: int | None = None) -> PriceSpell:
    return PriceSpell(
        Decimal(price), day(start), None if end is None else day(end)
    )


def evaluate(spells, *, now=NOW, is_reduction=True, episode=None):
    return evaluate_reduction(
        spells=spells,
        first_offered_at=spells[0].valid_from,
        is_reduction=is_reduction,
        episode=episode,
        now=now,
    )


class TestReference:
    def test_no_reduction_means_no_episode(self):
        spells = [spell("100", -90, 0), spell("80", 0)]

        assert evaluate(spells, is_reduction=False) is None

    def test_reference_is_the_lowest_price_of_the_30_days_before(self):
        spells = [
            spell("120", -90, -25),
            spell("90", -25, -20),
            spell("100", -20, 0),
            spell("80", 0),
        ]

        episode = evaluate(spells)

        assert episode == ReductionEpisode(NOW, Decimal(90))

    def test_a_lower_price_older_than_30_days_is_ignored(self):
        spells = [
            spell("50", -90, -31),
            spell("100", -31, 0),
            spell("80", 0),
        ]

        episode = evaluate(spells)

        assert episode is not None
        assert episode.prior_price == Decimal(100)

    def test_the_reduction_s_own_price_is_not_its_reference(self):
        # The new spell starts at ``now``: the window is [now-30d, now).
        spells = [spell("100", -90, 0), spell("100", 0)]

        assert evaluate(spells) is None

    def test_a_price_not_below_the_30_day_low_is_no_reduction(self):
        spells = [
            spell("90", -90, -10),
            spell("70", -10, 0),
            spell("80", 0),
        ]

        assert evaluate(spells) is None

    def test_reference_is_frozen_at_the_start(self):
        spells = [spell("100", -90, 0), spell("80", 0)]
        episode = evaluate(spells)

        later = NOW + timedelta(days=45)
        again = evaluate(spells, now=later, episode=episode)

        assert again == episode
        assert again.prior_price == Decimal(100)

    def test_reevaluating_an_unchanged_episode_is_idempotent(self):
        spells = [spell("100", -90, 0), spell("80", 0)]
        episode = evaluate(spells)

        assert evaluate(spells, episode=episode) == episode


class TestYoungProducts:
    def test_a_product_on_sale_for_under_30_days_announces_nothing(self):
        spells = [spell("100", -10, 0), spell("80", 0)]

        episode = evaluate(spells)

        assert episode == ReductionEpisode(NOW, None)

    def test_exactly_30_days_on_sale_may_announce(self):
        spells = [spell("100", -30, 0), spell("80", 0)]

        episode = evaluate(spells)

        assert episode is not None
        assert episode.prior_price == Decimal(100)

    def test_a_reduction_that_started_young_stays_silent_when_it_ages(self):
        spells = [spell("100", -10, 0), spell("80", 0)]
        episode = evaluate(spells)

        later = NOW + timedelta(days=40)
        again = evaluate(spells, now=later, episode=episode)

        assert again == ReductionEpisode(NOW, None)


class TestProgressiveReductions:
    def _first_reduction(self):
        spells = [spell("100", -90, 0), spell("80", 0)]
        return spells, evaluate(spells)

    def test_a_further_drop_within_60_days_keeps_the_reference(self):
        _spells, episode = self._first_reduction()
        spells = [
            spell("100", -90, 0),
            spell("80", 0, 30),
            spell("70", 30),
        ]

        again = evaluate(spells, now=day(30), episode=episode)

        assert again == ReductionEpisode(NOW, Decimal(100))
        assert announced_discount_percent(again.prior_price, Decimal(70)) == 30

    def test_a_drop_after_60_days_re_anchors(self):
        _spells, episode = self._first_reduction()
        spells = [
            spell("100", -90, 0),
            spell("80", 0, 61),
            spell("70", 61),
        ]

        again = evaluate(spells, now=day(61), episode=episode)

        # The new reference is the lowest price of the 30 days before the
        # new drop: the 80 the product has been at since the first one.
        assert again == ReductionEpisode(day(61), Decimal(80))

    def test_the_60_days_run_from_the_first_reduction_not_the_last(self):
        spells = [
            spell("100", -90, 0),
            spell("80", 0, 40),
            spell("70", 40, 70),
            spell("60", 70),
        ]
        episode = ReductionEpisode(NOW, Decimal(100))

        again = evaluate(spells, now=day(70), episode=episode)

        assert again is not None
        assert again.started_at == day(70)
        assert again.prior_price == Decimal(70)

    def test_any_increase_breaks_the_chain(self):
        _spells, episode = self._first_reduction()
        spells = [
            spell("100", -90, 0),
            spell("80", 0, 10),
            spell("90", 10),
        ]

        again = evaluate(spells, now=day(10), episode=episode)

        # 90 is not below the 80 of the last 30 days: nothing to announce.
        assert again is None

    def test_a_drop_after_an_increase_is_a_new_reduction(self):
        spells = [
            spell("100", -90, 0),
            spell("80", 0, 10),
            spell("90", 10, 20),
            spell("70", 20),
        ]

        again = evaluate(spells, now=day(20), episode=None)

        # The reference is the 80 of ten days ago, not the original 100,
        # although the first reduction is only 20 days old.
        assert again == ReductionEpisode(day(20), Decimal(80))

    def test_a_price_reaching_the_reference_ends_the_episode(self):
        spells = [spell("100", -90, -5), spell("100", -5)]
        episode = ReductionEpisode(day(-40), Decimal(100))

        assert evaluate(spells, episode=episode) is None


class TestInterruption:
    def test_a_reduction_that_stops_ends_the_episode(self):
        spells = [spell("100", -90, 0), spell("80", 0, 5), spell("100", 5)]
        episode = ReductionEpisode(NOW, Decimal(100))

        assert (
            evaluate(spells, now=day(5), is_reduction=False, episode=episode)
            is None
        )

    def test_the_next_reduction_gets_a_new_reference(self):
        # First reduction: days 0-5 at 80. The second one starts on day
        # 40, so the 80 is outside its 30-day window and the reference is
        # the 100 in between.
        spells = [
            spell("100", -90, 0),
            spell("80", 0, 5),
            spell("100", 5, 40),
            spell("75", 40),
        ]

        episode = evaluate(spells, now=day(40), episode=None)

        assert episode == ReductionEpisode(day(40), Decimal(100))

    def test_a_quick_comeback_is_measured_against_the_earlier_reduction(self):
        spells = [
            spell("100", -90, 0),
            spell("80", 0, 5),
            spell("100", 5, 10),
            spell("80", 10),
        ]

        # The 80 of five days ago is inside the window, so 80 is not a
        # reduction from it.
        assert evaluate(spells, now=day(10), episode=None) is None


class TestAnnouncedPercent:
    @pytest.mark.parametrize(
        ("prior", "current", "expected"),
        [
            ("100", "80", 20),
            ("100", "79.99", 20),
            ("100", "80.01", 19),
            ("100", "99.99", 0),
            ("19.90", "15.92", 20),
            ("100", "100", None),
            ("100", "120", None),
            ("0", "0", None),
        ],
    )
    def test_floor_of_the_drop_from_the_prior_price(
        self, prior, current, expected
    ):
        assert (
            announced_discount_percent(Decimal(prior), Decimal(current))
            == expected
        )

    def test_nothing_is_announced_without_a_prior_price(self):
        assert announced_discount_percent(None, Decimal(80)) is None
        assert announced_discount_percent(Decimal(100), None) is None
