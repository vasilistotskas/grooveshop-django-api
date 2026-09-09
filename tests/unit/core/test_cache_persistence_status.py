"""Redis is the only thing that knows it can no longer reach its disk.

A ``set``/``get`` round-trip is served from memory and keeps passing
through a completely full volume — which is why every check we had
stayed green on 2026-09-09 while Redis crash-looped 42 times on "No
space left on device" and webside.gr served 503s.

``INFO persistence`` is where Redis says so: each save path reports a
status that reads ``ok`` until a write fails and ``err`` afterwards.
These tests pin the reading of it, including the two ways a naive
version gets it wrong — judging an RDB-only server by AOF fields it
does not use, and inventing a failure out of a field the server never
reported.
"""

from __future__ import annotations

from unittest.mock import Mock

from core.caches import CustomCache

HEALTHY_AOF = {
    "loading": 0,
    "rdb_last_bgsave_status": "ok",
    "aof_enabled": 1,
    "aof_last_write_status": "ok",
    "aof_last_bgrewrite_status": "ok",
}


def _cache_reporting(info: dict) -> CustomCache:
    """A backend whose Redis answers ``INFO persistence`` with ``info``."""
    cache = CustomCache.__new__(CustomCache)
    client = Mock()
    client.info.return_value = info
    cache._cache = Mock(get_client=Mock(return_value=client))
    return cache


class TestNothingIsReportedWhileHealthy:
    def test_an_all_ok_server_reports_no_failures(self):
        assert (
            _cache_reporting(HEALTHY_AOF).failing_persistence_statuses() == {}
        )

    def test_it_asks_redis_only_for_the_persistence_section(self):
        """``INFO`` with no argument returns every section — hundreds of
        fields, several of which are O(n) to compute."""
        cache = _cache_reporting(HEALTHY_AOF)
        cache.failing_persistence_statuses()

        cache._cache.get_client().info.assert_called_with("persistence")


class TestAFailedWriteIsReported:
    def test_a_failed_aof_write_is_named(self):
        """The exact failure that preceded the outage."""
        statuses = _cache_reporting(
            HEALTHY_AOF | {"aof_last_write_status": "err"}
        ).failing_persistence_statuses()

        assert statuses == {"aof_last_write_status": "err"}

    def test_a_failed_aof_rewrite_is_named(self):
        statuses = _cache_reporting(
            HEALTHY_AOF | {"aof_last_bgrewrite_status": "err"}
        ).failing_persistence_statuses()

        assert statuses == {"aof_last_bgrewrite_status": "err"}

    def test_a_failed_rdb_save_is_named_even_with_aof_on(self):
        statuses = _cache_reporting(
            HEALTHY_AOF | {"rdb_last_bgsave_status": "err"}
        ).failing_persistence_statuses()

        assert statuses == {"rdb_last_bgsave_status": "err"}

    def test_every_failing_field_is_returned_not_just_the_first(self):
        """A full volume fails the write and the rewrite together, and
        an alert naming one of them understates the problem."""
        statuses = _cache_reporting(
            HEALTHY_AOF
            | {
                "aof_last_write_status": "err",
                "aof_last_bgrewrite_status": "err",
                "rdb_last_bgsave_status": "err",
            }
        ).failing_persistence_statuses()

        assert statuses == {
            "aof_last_write_status": "err",
            "aof_last_bgrewrite_status": "err",
            "rdb_last_bgsave_status": "err",
        }


class TestItJudgesOnlyWhatTheServerRuns:
    def test_aof_fields_are_ignored_when_aof_is_disabled(self):
        """Redis reports the AOF pair whether or not AOF is enabled.

        An RDB-only server that has never rewritten an AOF must not be
        called unhealthy for it — that alert would fire every 30
        minutes forever and train everyone to ignore the channel.
        """
        statuses = _cache_reporting(
            {
                "rdb_last_bgsave_status": "ok",
                "aof_enabled": 0,
                "aof_last_write_status": "err",
                "aof_last_bgrewrite_status": "err",
            }
        ).failing_persistence_statuses()

        assert statuses == {}

    def test_a_field_the_server_never_reported_is_not_a_failure(self):
        """Absence is a Redis build difference, not an error. Defaulting
        a missing field to "err" would page on a version bump."""
        statuses = _cache_reporting(
            {"aof_enabled": 1, "aof_last_write_status": "ok"}
        ).failing_persistence_statuses()

        assert statuses == {}

    def test_a_connection_failure_propagates(self):
        """The caller reports an unreachable Redis as a cache failure
        already; swallowing it here would turn "Redis is gone" into
        "Redis persists fine"."""
        cache = CustomCache.__new__(CustomCache)
        client = Mock()
        client.info.side_effect = ConnectionError("redis is gone")
        cache._cache = Mock(get_client=Mock(return_value=client))

        try:
            cache.failing_persistence_statuses()
        except ConnectionError:
            pass
        else:
            raise AssertionError("expected the ConnectionError to propagate")
