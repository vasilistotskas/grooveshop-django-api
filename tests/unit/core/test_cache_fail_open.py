"""An unreachable Redis costs speed, not requests.

``CustomCache`` treats redis-py's ``ConnectionError`` / ``TimeoutError``
as a miss on reads and as a logged no-op on writes and deletes, with one
WARNING per burst. What must NOT be softened: the lock and counter
primitives (``add``, ``incr``), whose result is a decision, and the
admin and health operations whose failure has to be seen (``clear``,
``keys``, ``clear_by_prefixes``, ``failing_persistence_statuses``).

Two ways to fail: a real client pointed at a closed port (the stopped
server), and a client whose every command times out.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock, patch

import pytest
from redis.backoff import NoBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError
from redis.exceptions import TimeoutError as RedisTimeoutError
from redis.retry import Retry

from core import caches
from core.caches import CustomCache


@pytest.fixture(autouse=True)
def _fresh_burst_window(monkeypatch):
    monkeypatch.setattr(
        caches,
        "_burst_warning",
        caches.BurstLogger(caches._burst_warning._message),
    )


@pytest.fixture
def stopped():
    """A real client against a port nothing listens on."""
    return CustomCache(
        server="redis://127.0.0.1:1/0",
        params={
            "OPTIONS": {
                "retry": Retry(NoBackoff(), 0),
                "socket_connect_timeout": 0.2,
                "health_check_interval": 0,
            }
        },
    )


@pytest.fixture
def timing_out():
    """Every command times out."""
    cache = CustomCache(server="redis://127.0.0.1:1/0", params={})
    client = MagicMock()
    for command in (
        "get",
        "set",
        "mget",
        "exists",
        "delete",
        "expire",
        "persist",
        "flushdb",
        "scan_iter",
        "info",
    ):
        getattr(client, command).side_effect = RedisTimeoutError("timed out")
    client.pipeline.return_value.execute.side_effect = RedisTimeoutError(
        "timed out"
    )
    fake = MagicMock()
    fake.get_client.return_value = client
    fake.get.side_effect = RedisTimeoutError("timed out")
    fake.set.side_effect = RedisTimeoutError("timed out")
    fake.get_many.side_effect = RedisTimeoutError("timed out")
    fake.has_key.side_effect = RedisTimeoutError("timed out")
    fake.set_many.side_effect = RedisTimeoutError("timed out")
    fake.touch.side_effect = RedisTimeoutError("timed out")
    fake.delete.side_effect = RedisTimeoutError("timed out")
    fake.delete_many.side_effect = RedisTimeoutError("timed out")
    fake.add.side_effect = RedisTimeoutError("timed out")
    fake.incr.side_effect = RedisTimeoutError("timed out")
    fake.clear.side_effect = RedisTimeoutError("timed out")
    # ``_cache`` is a ``cached_property``: seed the instance dict.
    cache.__dict__["_cache"] = fake
    return cache


@pytest.fixture(params=["stopped", "timing_out"])
def down(request):
    return request.getfixturevalue(request.param)


class TestReadsMiss:
    def test_get_returns_the_default(self, down):
        assert down.get("k") is None
        assert down.get("k", "fallback") == "fallback"

    def test_get_many_is_empty(self, down):
        assert down.get_many(["a", "b"]) == {}

    def test_has_key_and_contains_are_false(self, down):
        assert down.has_key("k") is False
        assert ("k" in down) is False

    def test_get_or_set_computes_the_default_once(self, down):
        calls = []

        def compute():
            calls.append(1)
            return "computed"

        assert down.get_or_set("k", compute) == "computed"
        assert calls == [1]

    def test_the_async_api_fails_open_too(self, down):
        # ``BaseCache.a*`` are ``sync_to_async`` over the sync methods.
        assert asyncio.run(down.aget("k", "fallback")) == "fallback"
        asyncio.run(down.aset("k", "v"))
        assert asyncio.run(down.adelete("k")) is False


class TestWritesAreNoOps:
    def test_set_and_delete(self, down):
        assert down.set("k", "v") is None
        assert down.delete("k") is False
        assert down.delete_many(["a", "b"]) is None
        assert down.touch("k") is False

    def test_set_many_reports_every_key_as_failed(self, down):
        # Django's contract: ``set_many`` returns the keys it could not
        # insert.
        assert sorted(down.set_many({"a": 1, "b": 2})) == ["a", "b"]


class TestDecisionsAndAdminStillRaise:
    def test_add_raises(self, down):
        # The lock primitive: neither "acquired" nor "held" is a safe lie.
        with pytest.raises((RedisConnectionError, RedisTimeoutError)):
            down.add("lock", 1, 10)

    def test_incr_raises(self, down):
        with pytest.raises((RedisConnectionError, RedisTimeoutError)):
            down.incr("counter")

    def test_clear_raises(self, down):
        with pytest.raises((RedisConnectionError, RedisTimeoutError)):
            down.clear()

    def test_keys_raises(self, down):
        with pytest.raises((RedisConnectionError, RedisTimeoutError)):
            down.keys("anything")

    def test_clear_by_prefixes_raises(self, down):
        with pytest.raises((RedisConnectionError, RedisTimeoutError)):
            down.clear_by_prefixes(["redis:1:"])

    def test_persistence_probe_raises(self, down):
        with pytest.raises((RedisConnectionError, RedisTimeoutError)):
            down.failing_persistence_statuses()


class TestOnlyUnavailabilityIsAbsorbed:
    def test_a_command_error_still_raises(self, timing_out):
        timing_out._cache.get.side_effect = ResponseError("WRONGTYPE")

        with pytest.raises(ResponseError):
            timing_out.get("k")


class TestOneWarningPerBurst:
    def test_a_burst_logs_once(self, stopped, caplog):
        with caplog.at_level(logging.WARNING, logger="core.caches"):
            for _ in range(5):
                stopped.get("k")
                stopped.set("k", "v")

        warnings = [
            r for r in caplog.records if "Redis cache unavailable" in r.message
        ]
        assert len(warnings) == 1

    def test_the_next_window_logs_again_with_the_suppressed_count(
        self, stopped, caplog
    ):
        clock = iter([1000.0, 1001.0, 1002.0, 1100.0])
        with (
            patch("core.caches.time.monotonic", lambda: next(clock)),
            caplog.at_level(logging.WARNING, logger="core.caches"),
        ):
            for _ in range(4):
                stopped.get("k")

        warnings = [
            r.getMessage()
            for r in caplog.records
            if "Redis cache unavailable" in r.message
        ]
        assert len(warnings) == 2
        assert "(2 similar failure(s) suppressed" in warnings[1]


class TestStrictKeysStillRaise:
    """A miss on these would switch a security control off."""

    @pytest.mark.parametrize(
        "key",
        [
            "strict:throttle_gift_card_check_user:1",
            "allauth:rl:login:ip:127.0.0.1",
            "allauth.mfa.totp.used?user=1&code=123456",
            "allauth.idp.oidc.authorization_code[c:x]",
            "allauth.idp.oidc.device_code[d]",
            "ws:ticket:abc",
        ],
    )
    def test_reads_writes_and_deletes_raise(self, down, key):
        errors = (RedisConnectionError, RedisTimeoutError)
        with pytest.raises(errors):
            down.get(key)
        with pytest.raises(errors):
            down.set(key, "v")
        with pytest.raises(errors):
            down.delete(key)
        with pytest.raises(errors):
            down.get_many(["plain", key])
        with pytest.raises(errors):
            down.set_many({"plain": 1, key: 2})
        with pytest.raises(errors):
            down.delete_many(["plain", key])

    def test_an_ordinary_key_still_fails_open(self, down):
        assert down.get("throttle_anon_1.2.3.4", []) == []
