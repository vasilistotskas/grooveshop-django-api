"""Exception classes for core infrastructure tasks.

Follows the per-app ``exceptions.py`` convention already used by
``order``, ``shipping*`` and ``meta_capi``.

These replace bare ``raise Exception(...)`` in the health-check and
maintenance tasks. The distinction matters because a bare Exception cannot
tell "the thing I am monitoring is unhealthy" — a real signal worth
retrying — from "this task has a bug", which retrying only repeats.
``StorePagesPurgeFailed`` is the one a task retries (``autoretry_for``);
``HealthCheckFailed`` belongs to ``monitor_system_health``, which
deliberately never retries, and ``ManagementCommandFailed`` is
deterministic, so neither is in a retry set.

Exception Hierarchy:
    CoreTaskError (base)
    ├── HealthCheckFailed    — a monitored dependency reported unhealthy
    ├── ManagementCommandFailed — a wrapped `call_command` did not succeed
    └── StorePagesPurgeFailed — the storefront did not purge a store's pages
"""

from __future__ import annotations


class CoreTaskError(Exception):
    """Base class for failures raised by core's scheduled tasks."""


class HealthCheckFailed(CoreTaskError):
    """A monitored dependency (database, cache, broker) is unhealthy.

    Raised so the task fails loudly — a health check that swallows its
    own failure reports the system healthy, which is the one outcome a
    health check must never produce.
    """

    def __init__(self, component: str, detail: str | None = None):
        self.component = component
        self.detail = detail
        super().__init__(
            f"Health check failed for {component}"
            + (f": {detail}" if detail else "")
        )


class StorePagesPurgeFailed(CoreTaskError):
    """The storefront's purge endpoint failed to purge a store's pages.

    Raised so the purge is retried: until it succeeds, the store keeps
    showing a feature it has just switched off.
    """

    def __init__(self, surfaces: list[str]):
        self.surfaces = surfaces
        super().__init__(f"Store page purge failed for {surfaces}")


class ManagementCommandFailed(CoreTaskError):
    """A management command invoked from a task exited non-zero."""

    def __init__(self, command: str, code: object):
        self.command = command
        self.code = code
        super().__init__(f"{command} exited with code {code}")
