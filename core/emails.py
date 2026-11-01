"""The operators' alerts about the platform itself: a failed health check
and a failed backup. Their types, builders and samples.

Sent from ``public`` to ``settings.ADMINS`` in the platform's language.
``emails.EmailsConfig`` imports this module at boot; ``core.tasks`` chooses
WHEN to send.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.utils.translation import gettext_lazy as _
from django.utils.translation import gettext_noop

from emails.declare import operator_alert
from emails.types import RenderEnv
from tenant.credentials import tenant_site_name

TEMPLATE_DIR = "emails/platform"


@dataclass(frozen=True, slots=True)
class HealthAlert:
    """The components that failed the health check, and what each said."""

    failed: tuple[str, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BackupFailed:
    """The scheduled database backup could not be dispatched."""

    error: str


def _health_alert(alert: HealthAlert, env: RenderEnv) -> dict[str, Any]:
    return {
        "site": tenant_site_name(),
        "components": ", ".join(alert.failed),
        "errors": alert.errors,
    }


def _backup_failed(alert: BackupFailed, env: RenderEnv) -> dict[str, Any]:
    return {"site": tenant_site_name(), "error": alert.error}


operator_alert(
    "platform.health_alert",
    title=_("System health check failed"),
    description=_(
        "Tells the platform's operators that the database, the cache or "
        "the media storage failed its health check."
    ),
    templates=f"{TEMPLATE_DIR}/health_alert",
    subject=gettext_noop(
        "[{site}] CRITICAL: System Health Check Failed ({components})"
    ),
    build_context=_health_alert,
    sample=lambda env: HealthAlert(
        failed=("cache",),
        errors=(
            (
                "Cache health check failed: Redis cannot persist to disk "
                "(aof_last_write_status=err) — check free space on its volume"
            ),
        ),
    ),
)
operator_alert(
    "platform.backup_failed",
    title=_("Scheduled backup failed"),
    description=_(
        "Tells the platform's operators that the scheduled database "
        "backup failed."
    ),
    templates=f"{TEMPLATE_DIR}/backup_failed",
    subject=gettext_noop("[{site}] ALERT: Scheduled Database Backup Failed"),
    build_context=_backup_failed,
    sample=lambda env: BackupFailed(error="Broker unreachable"),
)
