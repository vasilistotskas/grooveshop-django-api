"""``core.tickets``: single-use tickets, spent atomically, kept apart by
purpose, and never failing open."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.core.cache import cache

from core import tickets


def test_a_ticket_names_its_user_once():
    ticket = tickets.mint(tickets.WEBSOCKET, 42, 60)

    assert tickets.spend(tickets.WEBSOCKET, ticket) == 42
    assert tickets.spend(tickets.WEBSOCKET, ticket) is None


def test_a_ticket_is_long_and_unique():
    first = tickets.mint(tickets.WEBSOCKET, 1, 60)
    second = tickets.mint(tickets.WEBSOCKET, 1, 60)

    assert first != second
    assert len(first) >= 40


def test_a_ticket_of_one_purpose_cannot_be_spent_as_another():
    ticket = tickets.mint(tickets.WEBSOCKET, 7, 60)

    assert tickets.spend(tickets.BUILDER_EDITOR, ticket) is None
    assert tickets.spend(tickets.WEBSOCKET, ticket) == 7


@pytest.mark.parametrize("ticket", ["", "never-minted", "x" * 100])
def test_an_unknown_ticket_names_nobody(ticket):
    assert tickets.spend(tickets.WEBSOCKET, ticket) is None


def test_an_unknown_purpose_is_a_bug_not_a_miss():
    with pytest.raises(ValueError, match="purpose"):
        tickets.mint("anything", 1, 60)
    with pytest.raises(ValueError, match="purpose"):
        tickets.spend("anything", "t")


def test_a_ticket_expires():
    ticket = tickets.mint(tickets.WEBSOCKET, 5, 60)
    key = cache.make_and_validate_key(tickets._key(tickets.WEBSOCKET, ticket))

    ttl = cache._cache.get_client(key).ttl(key)

    assert 0 < ttl <= 60


def test_a_value_that_is_not_a_user_id_names_nobody():
    key = tickets._key(tickets.WEBSOCKET, "garbled")
    cache.set(key, "not-an-int", 60)

    assert tickets.spend(tickets.WEBSOCKET, "garbled") is None
    assert cache.get(key) is None, "a spent ticket is gone either way"


def test_of_many_concurrent_spends_exactly_one_wins():
    ticket = tickets.mint(tickets.WEBSOCKET, 9, 60)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda _: tickets.spend(tickets.WEBSOCKET, ticket), range(32)
            )
        )

    assert [r for r in results if r is not None] == [9]


def test_every_ticket_key_is_under_the_namespace_that_never_fails_open():
    from core.caches import STRICT_NAMESPACE, _is_strict

    for purpose in tickets.PURPOSES:
        assert _is_strict(tickets._key(purpose, "t"))
        assert tickets._key(purpose, "t").startswith(STRICT_NAMESPACE)
