from __future__ import annotations

import logging
import threading
import time
from collections.abc import Awaitable
from typing import Any, cast

from django.conf import settings
from django.core.cache.backends.base import DEFAULT_TIMEOUT
from django.core.cache.backends.redis import RedisCache, RedisCacheClient
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from redis.retry import Retry

logger = logging.getLogger(__name__)

# The two failures that mean "Redis is unreachable", as opposed to a bad
# command or a serialisation bug, which must keep raising. redis-py's
# own classes (``redis/exceptions.py``), not the builtins of the same
# name; ``Retry`` re-raises the last of them once its attempts run out.
REDIS_UNAVAILABLE = (RedisConnectionError, RedisTimeoutError)

# One line per burst: an outage fails every cache call on every request,
# and a line per call would bury the log it is meant to be in.
BURST_INTERVAL_SECONDS = 60.0

#: Namespace for a key whose reads and writes must NEVER fail open. Code
#: that needs "Redis down means stop" rather than "Redis down means
#: miss" puts its keys under it (``core.api.throttling`` does, for every
#: throttle it defines).
STRICT_NAMESPACE = "strict:"

#: Every key the fail-open layer must leave raising. A miss on these is
#: not "slower", it is a security control switched off:
#:
#: - ``strict:`` — our own opt-in namespace (above).
#: - ``allauth:rl:`` — allauth's rate limits: login, signup, password
#:   reset, email confirmation and MFA attempts
#:   (``allauth/core/internal/ratelimit.py`` ``get_cache_key``). Failing
#:   open would lift the brute-force limit on login.
#: - ``allauth.mfa.totp.used?`` — TOTP replay protection
#:   (``allauth/mfa/totp/internal/auth.py``): a miss reports a used code
#:   as unused.
#: - ``allauth.idp.oidc.authorization_code[`` / ``user_code[`` /
#:   ``device_code[`` — the OAuth IdP's single-use codes
#:   (``allauth/idp/oidc/internal/oauthlib/``): a lost ``delete`` leaves a
#:   redeemed code redeemable until its TTL.
#: - ``ws:ticket:`` — the single-use WebSocket ticket
#:   (``notification/views/websocket.py``): a lost ``set`` hands the
#:   browser a ticket that can never be redeemed, so the mint must fail
#:   visibly instead.
#:
#: These are third-party or fixed keys — allauth reads the default alias
#: directly and offers no setting to point it elsewhere — so the match is
#: on the key, in this one place.
STRICT_KEY_PREFIXES: tuple[str, ...] = (
    STRICT_NAMESPACE,
    "allauth:rl:",
    "allauth.mfa.totp.used?",
    "allauth.idp.oidc.authorization_code[",
    "allauth.idp.oidc.user_code[",
    "allauth.idp.oidc.device_code[",
    "ws:ticket:",
)


def _is_strict(key: Any) -> bool:
    return isinstance(key, str) and key.startswith(STRICT_KEY_PREFIXES)


class BurstLogger:
    """Log the first event of a burst, then one line per interval.

    Process-wide rather than per backend instance: Django hands every
    thread its own cache object (``django.core.cache.CacheHandler`` is a
    ``BaseConnectionHandler`` over ``asgiref.local.Local``), so an
    instance counter would log once per thread instead of once per burst.

    ``message`` is a %-format taking the caller's ``args`` followed by
    the suppressed count and the interval in seconds.
    """

    def __init__(
        self,
        message: str,
        *,
        level: int = logging.WARNING,
        target: logging.Logger | None = None,
    ) -> None:
        self._message = message
        self._level = level
        self._logger = target or logger
        self._lock = threading.Lock()
        self._last_logged = float("-inf")
        self._suppressed = 0

    def report(self, *args: Any) -> None:
        now = time.monotonic()
        with self._lock:
            if now - self._last_logged < BURST_INTERVAL_SECONDS:
                self._suppressed += 1
                return
            suppressed, self._suppressed = self._suppressed, 0
            self._last_logged = now
        self._logger.log(
            self._level,
            self._message,
            *args,
            suppressed,
            int(BURST_INTERVAL_SECONDS),
        )


_burst_warning = BurstLogger(
    "Redis cache unavailable during %s — serving without the cache: %s "
    "(%s similar failure(s) suppressed in the last %ss)"
)


_SCAN_BATCH_SIZE = 500

# ``INFO persistence`` reports each save path's outcome as a status
# string that reads "ok" while healthy and "err" once the write failed.
# Names are Redis's, not ours — see ``failing_persistence_statuses``.
_STATUS_OK = "ok"
_RDB_STATUS_FIELD = "rdb_last_bgsave_status"
_AOF_STATUS_FIELDS = ("aof_last_write_status", "aof_last_bgrewrite_status")

# redis-py ships every resilience feature OFF by default: the sync
# Connection defaults are ``retry=None``, ``health_check_interval=0``
# and ``socket_keepalive=False``. Django never overrides them, so a
# pooled connection that died while idle — Redis ``timeout``, a pod
# rollover, a NAT/conntrack eviction — is handed straight back out and
# the next command raises ConnectionResetError at the call site.
#
# That is not a test-only concern: with no OPTIONS on ``CACHES`` the
# production cache had exactly the same exposure, so a single reset
# surfaced as a 500 rather than a retried command.
#
# - ``health_check_interval`` PINGs a connection idle longer than N
#   seconds before reuse and transparently reconnects a dead one.
# - ``socket_keepalive`` lets the OS notice a peer that vanished
#   without a FIN.
# - ``retry`` re-runs the command on ConnectionError/TimeoutError
#   (redis-py's default ``supported_errors``); the backoff is
#   sub-second in total, so a request never visibly stalls.
#
# The Retry template is shared, which is safe: redis-py deep-copies it
# per connection (``AbstractConnection.__init__``).
_RESILIENCE_OPTIONS: dict[str, Any] = {
    "health_check_interval": 30,
    "socket_keepalive": True,
    "retry": Retry(ExponentialBackoff(), retries=3),
}


class CustomCache(RedisCache):
    """
    Redis cache backend with prefix-aware clearing and key inspection.

    Fails open: with Redis unreachable, reads miss and writes do nothing
    (see "Fail-open reads and writes" below for exactly which methods) —
    except for keys under ``STRICT_KEY_PREFIXES``, which keep raising
    because a miss there switches a security control off.

    Provides:
    - ``clear_by_prefixes()`` -- selectively clear keys by prefix
      instead of FLUSHDB, safe for shared Redis instances. Platform-
      wide (all schemas) by design — used by the public-schema
      ``clear_all_cache`` beat task and the clear_cache command.
    - ``keys()`` / ``delete_raw_keys()`` -- admin cache-management
      utilities for pattern-based key inspection and deletion,
      scoped to the ACTIVE schema (raw layout per
      ``tenant.cache.make_tenant_key``: ``{schema}:{prefix}:{version}:
      {key}``) so one tenant's admin purge can never UNLINK another
      tenant's keys.

    Registered as ``CACHES["default"]`` so these helpers share the
    tenant-keyed backend every ``cache.get/set`` uses.
    """

    _cache: RedisCacheClient

    def __init__(self, server: Any, params: dict[str, Any]) -> None:
        """Apply the connection-resilience defaults.

        Set here rather than in ``CACHES["default"]["OPTIONS"]`` because
        this backend is also instantiated directly (tests, and any
        caller building a cache for a specific Redis DB), and those
        paths pass ``params={}`` — a settings-only fix would leave them
        on redis-py's bare defaults.

        Anything explicitly configured in ``OPTIONS`` still wins.
        ``params`` is copied, never mutated: Django hands us the live
        ``settings.CACHES`` entry.
        """
        params = {
            **params,
            "OPTIONS": {
                **_RESILIENCE_OPTIONS,
                **(params.get("OPTIONS") or {}),
            },
        }
        super().__init__(server, params)

    # ------------------------------------------------------------------
    # Fail-open reads and writes
    # ------------------------------------------------------------------
    #
    # A cache is an optimisation, so an unreachable Redis must cost
    # speed, not requests: a read is a miss, a write or delete does
    # nothing, and one WARNING per burst says so. Only
    # ``REDIS_UNAVAILABLE`` is absorbed — any other error is a real bug —
    # and never for a key under ``STRICT_KEY_PREFIXES``, where a miss
    # would switch a security control off (see that tuple).
    #
    # These are all the per-key methods ``RedisCache`` implements
    # (``django/core/cache/backends/redis.py``). ``BaseCache`` builds
    # the rest on them — ``decr`` on ``incr``, ``incr_version`` on
    # ``get``/``set``/``delete``, ``__contains__`` on ``has_key``, and
    # every ``a*`` method is ``sync_to_async`` over its sync twin
    # (``backends/base.py``) — so the async API fails open too.
    #
    # Consequence for rate limiting: DRF's ``SimpleRateThrottle`` keeps
    # its history with ``cache.get``/``cache.set`` on this alias
    # (``rest_framework/throttling.py``), so a throttle on its default
    # key ALLOWS every request during an outage. That is right for the
    # general ``anon``/``user`` budgets and wrong for the security
    # scopes, which is why every throttle in ``core.api.throttling``
    # keys under ``strict:`` and decides per class whether an outage
    # denies or allows (``ResilientThrottleMixin.fail_closed``).
    #
    # Deliberately left raising:
    # - ``add`` and ``incr``: their RESULT is a decision, not a value.
    #   ``add`` is the lock primitive (the order-confirmation send, the
    #   ACS pickup-list and poll-batch mutexes) and ``incr`` a counter;
    #   neither "acquired" nor "not acquired" is a safe lie, and their
    #   callers already handle the exception (the confirmation task
    #   retries; the rate limiter and idempotency budget decide on it
    #   themselves).
    # - ``clear`` (FLUSHDB), ``keys``, ``clear_by_prefixes`` and
    #   ``failing_persistence_statuses``: explicit admin and health
    #   operations whose failure must be seen — see ``keys()``.
    #   ``delete_raw_keys`` keeps its own documented handling.

    def get(self, key: Any, default: Any = None, version: int | None = None):
        try:
            return super().get(key, default, version)
        except REDIS_UNAVAILABLE as exc:
            if _is_strict(key):
                raise
            _burst_warning.report("get", exc)
            return default

    def get_many(self, keys: Any, version: int | None = None) -> dict:
        keys = list(keys)
        try:
            return super().get_many(keys, version)
        except REDIS_UNAVAILABLE as exc:
            if any(_is_strict(key) for key in keys):
                raise
            _burst_warning.report("get_many", exc)
            return {}

    def has_key(self, key: Any, version: int | None = None) -> bool:
        try:
            return super().has_key(key, version)
        except REDIS_UNAVAILABLE as exc:
            if _is_strict(key):
                raise
            _burst_warning.report("has_key", exc)
            return False

    def get_or_set(
        self,
        key: Any,
        default: Any,
        timeout: Any = DEFAULT_TIMEOUT,
        version: int | None = None,
    ) -> Any:
        """``BaseCache.get_or_set`` with an outage returning the default.

        The base implementation stores the value through ``add``, which
        raises on purpose (see above); without this, an outage would
        fail every ``get_or_set`` caller instead of computing the value.
        Same steps as the base otherwise, so a callable default is
        computed once.
        """
        value = self.get(key, self._missing_key, version=version)
        if value is not self._missing_key:
            return value
        if callable(default):
            default = default()
        try:
            self.add(key, default, timeout=timeout, version=version)
        except REDIS_UNAVAILABLE as exc:
            if _is_strict(key):
                raise
            _burst_warning.report("get_or_set", exc)
            return default
        # Read back, as the base does: another caller may have added a
        # value between the first ``get`` and the ``add``.
        return self.get(key, default, version=version)

    def set(
        self,
        key: Any,
        value: Any,
        timeout: Any = DEFAULT_TIMEOUT,
        version: int | None = None,
    ) -> None:
        try:
            super().set(key, value, timeout, version)
        except REDIS_UNAVAILABLE as exc:
            if _is_strict(key):
                raise
            _burst_warning.report("set", exc)

    def set_many(
        self,
        data: dict,
        timeout: Any = DEFAULT_TIMEOUT,
        version: int | None = None,
    ) -> list:
        try:
            return super().set_many(data, timeout, version)
        except REDIS_UNAVAILABLE as exc:
            if any(_is_strict(key) for key in data):
                raise
            _burst_warning.report("set_many", exc)
            # ``set_many`` returns the keys that failed to insert.
            return list(data)

    def touch(
        self,
        key: Any,
        timeout: Any = DEFAULT_TIMEOUT,
        version: int | None = None,
    ) -> bool:
        try:
            return super().touch(key, timeout, version)
        except REDIS_UNAVAILABLE as exc:
            if _is_strict(key):
                raise
            _burst_warning.report("touch", exc)
            return False

    def delete(self, key: Any, version: int | None = None) -> bool:
        try:
            return super().delete(key, version)
        except REDIS_UNAVAILABLE as exc:
            if _is_strict(key):
                raise
            _burst_warning.report("delete", exc)
            return False

    def delete_many(self, keys: Any, version: int | None = None) -> None:
        keys = list(keys)
        try:
            super().delete_many(keys, version)
        except REDIS_UNAVAILABLE as exc:
            if any(_is_strict(key) for key in keys):
                raise
            _burst_warning.report("delete_many", exc)

    def keys(self, search: str | None = None) -> list[str]:
        """
        Return raw Redis keys matching *search* via SCAN.

        The returned strings are the literal keys stored in Redis
        (e.g. ``redis:1:views.decorators.cache…``).  Use
        :meth:`delete_raw_keys` to remove them — **not** the regular
        ``delete()`` method which would re-apply ``make_key()``.

        Raises on a backend failure rather than returning ``[]``.
        Swallowing it here made every caller read a Redis outage as
        "nothing matched": ``CacheService._purge_surface`` wraps this
        call in an ``except Exception`` written to record exactly that
        failure, and the except could never fire, so the admin was told
        it had successfully purged zero keys.
        """
        pattern = self._make_pattern(search)
        raw_keys: list[str] = []
        for key in self._cache.get_client().scan_iter(
            match=pattern, count=_SCAN_BATCH_SIZE
        ):
            raw_keys.append(
                key.decode("utf-8") if isinstance(key, bytes) else key
            )
        raw_keys.sort()
        return raw_keys

    def delete_raw_keys(
        self, raw_keys: list[str]
    ) -> int | Awaitable[Any] | Any:
        """
        Delete keys directly in Redis without ``make_key`` transformation.

        Uses UNLINK (non-blocking) for better performance.
        Returns the number of keys actually deleted.
        """
        if not raw_keys:
            return 0
        try:
            client = self._cache.get_client()
            return client.unlink(*raw_keys)
        except Exception as exc:
            logger.warning("Error deleting raw cache keys: %s", str(exc))
            return 0

    def clear_by_prefixes(
        self, prefixes: list[str] | None = None
    ) -> dict[str, int]:
        """
        Selectively clear Redis keys matching the given prefixes.

        Unlike ``clear()`` (which calls FLUSHDB), this method only
        removes keys whose raw name starts with one of the specified
        prefixes.  Safe for shared Redis instances where other services
        store keys in the same database.

        Args:
            prefixes: Key prefixes to clear. Defaults to
                ``settings.CACHE_CLEAR_PREFIXES``.

        Returns:
            Dict mapping each prefix to the number of keys deleted.
        """
        if prefixes is None:
            prefixes = getattr(settings, "CACHE_CLEAR_PREFIXES", [])

        if not prefixes:
            logger.warning(
                "No prefixes configured for cache clearing "
                "(CACHE_CLEAR_PREFIXES is empty)"
            )
            return {}

        client = self._cache.get_client()
        results: dict[str, int] = {}

        for prefix in prefixes:
            # Two raw layouts share the Redis DB: Django keys carry the
            # tenant schema first ({schema}:{prefix}{key} via
            # make_tenant_key), while non-Django keys (e.g. Nuxt's
            # ``cache:``) start with the prefix directly. Scan both so
            # the platform-wide clear covers every schema.
            deleted = 0
            batch: list[str | bytes] = []
            for pattern in (f"{prefix}*", f"*:{prefix}*"):
                for key in client.scan_iter(
                    match=pattern, count=_SCAN_BATCH_SIZE
                ):
                    batch.append(key)
                    if len(batch) >= _SCAN_BATCH_SIZE:
                        deleted += cast(int, client.unlink(*batch))
                        batch.clear()

                if batch:
                    deleted += cast(int, client.unlink(*batch))
                    batch.clear()

            results[prefix] = deleted
            logger.info("Cleared %d keys with prefix '%s'", deleted, prefix)

        return results

    def failing_persistence_statuses(self) -> dict[str, str]:
        """Ask Redis whether it can still write its own data to disk.

        A ``set``/``get`` round-trip only proves Redis is answering
        from memory, and it keeps answering long after persistence has
        broken — so a read/write probe cannot see a full volume. Redis
        does report it: a failed ``BGSAVE`` or AOF write flips the
        matching ``INFO persistence`` field from ``ok`` to ``err`` and
        leaves it there until the next success.

        That flip is the precursor to an outage rather than the outage
        itself, which is the whole reason to watch it. On 2026-09-09
        the Redis volume filled, every AOF rewrite failed with "No
        space left on device", and only then did the liveness probe
        start killing the pod — 42 times, until webside.gr 503'd.
        Every check we had stayed green through all of it.

        Returns only the fields that are not ``ok`` — empty while
        healthy — so a caller can name the exact failure in an alert.
        Fields absent from this server's INFO are not reported: that
        is a Redis build difference, not a failure to persist.
        """
        info = self._cache.get_client().info("persistence")

        fields = [_RDB_STATUS_FIELD]
        # Redis reports the AOF pair whether or not AOF is on, so
        # consulting them unconditionally would judge an RDB-only
        # server by a subsystem it does not run.
        if info.get("aof_enabled"):
            fields.extend(_AOF_STATUS_FIELDS)

        return {
            field: str(info[field])
            for field in fields
            if field in info and str(info[field]) != _STATUS_OK
        }

    def _make_pattern(self, search: str | None = None) -> str:
        """SCAN pattern scoped to this backend's key namespace.

        Built through ``make_key`` so it follows the configured
        KEY_FUNCTION — with ``tenant.cache.make_tenant_key`` the
        pattern embeds the ACTIVE schema
        (``{schema}:{prefix}:{version}:…``), so admin purge and key
        inspection from a tenant admin only ever see that tenant's
        keys (and the platform admin only public's).
        """
        if search is None:
            return self.make_key("*")
        return self.make_key(f"*{search}*")
