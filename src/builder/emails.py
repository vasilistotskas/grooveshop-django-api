"""The page builder's emails: the alert for a scheduled publish that failed.

``emails.EmailsConfig`` imports this module at boot. Sending is decided by
``builder.scheduling`` (when the sweep marks a schedule failed).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.utils import formats, timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import gettext_noop

from builder.errors import message_for
from builder.models import BuilderScheduledPublish, document_label
from emails.declare import staff_email
from emails.types import RenderEnv
from tenant.credentials import tenant_site_name


@dataclass(frozen=True, slots=True)
class ScheduleFailed:
    """A scheduled publish that could not be released, with why."""

    schedule: BuilderScheduledPublish


def _failed(alert: ScheduleFailed, env: RenderEnv) -> dict[str, Any]:
    schedule = alert.schedule
    return {
        "site": tenant_site_name(),
        "documents": [document_label(key) for key in sorted(schedule.versions)],
        "scheduled_for": formats.date_format(
            timezone.localtime(schedule.scheduled_for), "DATETIME_FORMAT"
        ),
        "note": schedule.note,
        "problems": [
            message_for(
                problem["code"], problem.get("params"), problem["message"]
            )
            for problem in schedule.failure
        ],
    }


def _sample(env: RenderEnv) -> ScheduleFailed:
    return ScheduleFailed(
        BuilderScheduledPublish(
            scheduled_for=timezone.now().replace(
                hour=9, minute=0, second=0, microsecond=0
            ),
            versions={"template:home": 1, "group:header": 2},
            note="Spring sale",
            failure=[
                {
                    "path": "/sections/hero/settings/productId",
                    "code": "reference_not_found",
                    "message": "product 42 does not exist in this store.",
                    "document": "template:home",
                    "params": {"kind": "product", "target": "42"},
                }
            ],
        )
    )


staff_email(
    "ops.builder_schedule_failed",
    area="ops",
    title=_("A scheduled publish failed"),
    description=_(
        "Tells the store's staff that a page publish scheduled for a time "
        "could not go live, with the pages and the reasons."
    ),
    templates="emails/ops/builder_schedule_failed",
    subject=gettext_noop("[{site}] A scheduled publish failed"),
    build_context=_failed,
    sample=_sample,
)
