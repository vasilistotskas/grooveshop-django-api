"""Exception classes for core infrastructure tasks.

Follows the per-app ``exceptions.py`` convention already used by
``order``, ``shipping*`` and ``meta_capi``.

These replace bare ``raise Exception(...)`` in the health-check and
maintenance tasks. The distinction matters because those tasks declare
``autoretry_for=(Exception,)``: with a bare Exception there was no way to
tell "the thing I am monitoring is unhealthy" — a real signal worth
retrying — from "this task has a bug", which retrying only repeats.

Exception Hierarchy:
    CoreTaskError (base)
    ├── HealthCheckFailed    — a monitored dependency reported unhealthy
    └── ManagementCommandFailed — a wrapped `call_command` did not succeed
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


class ManagementCommandFailed(CoreTaskError):
    """A management command invoked from a task exited non-zero."""

    def __init__(self, command: str, code: object):
        self.command = command
        self.code = code
        super().__init__(f"{command} exited with code {code}")
