"""What the email page and the dashboard count: sends and failures per type.

One aggregate over ``EmailLog`` for the last 30 days, grouped by type, so the
page costs one query however many types the store has; the failures the
dashboard shows (7 days) read the same table. Cached for a minute and given
no ``depends_on``: log writes never stop, so a write-driven invalidation
would clear it on every send.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db.models import Count, Max, Q
from django.utils import timezone

from admin.dashboard.cache import dashboard_query
from emails.models import EmailLog
from emails.status import Status

#: The window of the page's "sent" and "failures" figures.
WINDOW_DAYS = 30
#: The window of the dashboard's "Emails failing".
FAILING_DAYS = 7

#: A message the provider has: a send that left (whatever became of it after).
SENT_STATUSES = (
    Status.SENT,
    Status.DELIVERED,
    Status.DELAYED,
    Status.BOUNCED,
    Status.COMPLAINED,
    Status.PROVIDER_SUPPRESSED,
)
#: What an owner has to look at: the mail did not arrive, or was held against.
FAILING_STATUSES = (
    Status.FAILED,
    Status.BOUNCED,
    Status.COMPLAINED,
    Status.PROVIDER_SUPPRESSED,
)


@dataclass(frozen=True, slots=True)
class TypeFigures:
    sent: int = 0
    failing: int = 0
    last_sent_at: datetime | None = None


@dashboard_query("emails:figures:v1", ttl=60)
def type_figures() -> dict[str, TypeFigures]:
    """Per type key, the last 30 days; a type with no row sent nothing."""
    since = timezone.now() - timedelta(days=WINDOW_DAYS)
    rows = (
        EmailLog.objects.filter(created_at__gte=since, is_test=False)
        .values("type_key")
        .annotate(
            sent=Count("pk", filter=Q(status__in=SENT_STATUSES)),
            failing=Count("pk", filter=Q(status__in=FAILING_STATUSES)),
            last_sent_at=Max("sent_at"),
        )
    )
    return {
        row["type_key"]: TypeFigures(
            row["sent"], row["failing"], row["last_sent_at"]
        )
        for row in rows
    }


def failing_since(days: int = FAILING_DAYS) -> Q:
    """The rows the dashboard counts and the log's "Failing" filter lists."""
    return Q(
        created_at__gte=timezone.now() - timedelta(days=days),
        status__in=FAILING_STATUSES,
        is_test=False,
    )
