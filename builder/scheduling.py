"""Publishing at a time: schedule, reschedule, cancel, and the sweep.

Scheduling validates the drafts in full, references included, and freezes
them as versions, so the release made later is what the merchant checked
(``BuilderScheduledPublish``). The sweep (``run_due``, a Celery beat task
per store, ``builder.tasks``) turns each due schedule into one release in
its own transaction, under the same per-store lock a publish takes. A row
is spent by the status change inside that transaction, so two sweeps (or a
sweep and a cancel) can never both act on it: ``select_for_update`` makes
the second one wait and then find it no longer pending.

The release is the ordinary one, so it moves the cache generation and the
``builder`` surface exactly as a publish does, at the time it happens.
Times are timezone-aware; a naive time sent by a client is read in the
store's timezone (``settings.TIME_ZONE``, Europe/Athens) by the API.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework import status

from builder import validation
from builder.errors import BuilderError, DocumentInvalid, Issue, NotFoundError
from builder.models import (
    BuilderDocument,
    BuilderScheduledPublish,
    BuilderVersion,
    ScheduleStatus,
    VersionKind,
)
from builder.services import (
    PUBLISH_PERMISSION,
    _catalogue,
    _create_release,
    _lock_release_sequence,
    _new_version,
    prune_unreleased_checkpoints,
    published_versions,
    require_permission,
    validated_drafts,
)
from tenant.staff_actors import staff_id

logger = logging.getLogger(__name__)


class NotPending(BuilderError):
    status_code = status.HTTP_409_CONFLICT
    default_code = "schedule_not_pending"
    default_detail = _("This schedule has already run or been cancelled.")


def _issue(
    code: str, message: str, path: str = "/scheduledFor"
) -> DocumentInvalid:
    return DocumentInvalid([Issue(path=path, code=code, message=message)])


def _checked_time(when: datetime) -> datetime:
    if timezone.is_naive(when):
        raise _issue("invalid_schedule_time", _("The time needs a timezone."))
    now = timezone.now()
    if when <= now:
        raise _issue("schedule_in_the_past", _("Choose a time in the future."))
    if when > now + timedelta(days=settings.BUILDER_SCHEDULE_MAX_DAYS):
        raise _issue(
            "schedule_too_far",
            _("A publish can be scheduled at most %(days)s days ahead.")
            % {"days": settings.BUILDER_SCHEDULE_MAX_DAYS},
        )
    return when


def schedule_publish(
    user: Any, *, keys: Iterable[str], when: datetime, note: str = ""
) -> BuilderScheduledPublish:
    """Release the drafts of *keys* together at *when*.

    The same checks as a publish (every draft valid, references existing,
    something to change), made now so a problem is the editor's to fix
    today and not a failed release at night.
    """
    require_permission(user, PUBLISH_PERMISSION)
    when = _checked_time(when)
    wanted = list(dict.fromkeys(keys))
    if not wanted:
        raise _issue(
            "nothing_to_publish",
            _("Choose at least one document to publish."),
            "/documents",
        )
    catalogue = _catalogue()
    with transaction.atomic():
        pending = BuilderScheduledPublish.objects.filter(
            status=ScheduleStatus.PENDING
        ).count()
        if pending >= settings.BUILDER_MAX_PENDING_SCHEDULES:
            raise _issue(
                "too_many_schedules",
                _("At most %(limit)s publishes can wait at once.")
                % {"limit": settings.BUILDER_MAX_PENDING_SCHEDULES},
                "",
            )
        documents, checked = validated_drafts(wanted, catalogue)
        versions = {
            document.key: _new_version(
                user,
                document,
                checked[document.key],
                kind=VersionKind.CHECKPOINT,
                note=note,
                catalogue_hash=catalogue.hash,
            ).pk
            for document in documents
        }
        if versions == {key: published_versions().get(key) for key in versions}:
            raise _issue(
                "nothing_to_publish",
                _("Nothing has changed since the last release."),
                "/documents",
            )
        schedule = BuilderScheduledPublish.objects.create(
            scheduled_for=when,
            versions=versions,
            revisions={d.key: d.draft_revision for d in documents},
            note=note,
            created_by_staff_id=staff_id(user),
        )
        for document in documents:
            prune_unreleased_checkpoints(document)
    return schedule


def _pending(pk: int) -> BuilderScheduledPublish:
    schedule = (
        BuilderScheduledPublish.objects.select_for_update()
        .filter(pk=pk)
        .first()
    )
    if schedule is None:
        raise NotFoundError(_("There is no such schedule."))
    if schedule.status != ScheduleStatus.PENDING:
        raise NotPending()
    return schedule


def reschedule(
    user: Any, *, pk: int, when: datetime
) -> BuilderScheduledPublish:
    require_permission(user, PUBLISH_PERMISSION)
    when = _checked_time(when)
    with transaction.atomic():
        schedule = _pending(pk)
        schedule.scheduled_for = when
        schedule.save(update_fields=["scheduled_for", "updated_at"])
    return schedule


def cancel(user: Any, *, pk: int) -> BuilderScheduledPublish:
    """Cancel a schedule that has not run. Its frozen versions stay, as
    any named save does, until retention takes them."""
    require_permission(user, PUBLISH_PERMISSION)
    with transaction.atomic():
        schedule = _pending(pk)
        schedule.status = ScheduleStatus.CANCELLED
        schedule.save(update_fields=["status", "updated_at"])
    return schedule


def _fail(schedule: BuilderScheduledPublish, issues: list[Issue]) -> None:
    schedule.status = ScheduleStatus.FAILED
    schedule.executed_at = timezone.now()
    schedule.failure = [issue.as_dict() for issue in issues]
    schedule.save(
        update_fields=["status", "executed_at", "failure", "updated_at"]
    )
    logger.warning(
        "Scheduled publish %s failed: %s",
        schedule.pk,
        [issue.code for issue in issues],
    )


def execute(pk: int) -> int | None:
    """Release schedule *pk* if it is pending and due; its release number,
    or ``None`` when there was nothing to do or it failed (recorded on the
    row). Safe to call twice and from two workers."""
    with transaction.atomic():
        _lock_release_sequence()
        schedule = (
            BuilderScheduledPublish.objects.select_for_update()
            .filter(
                pk=pk,
                status=ScheduleStatus.PENDING,
                scheduled_for__lte=timezone.now(),
            )
            .first()
        )
        if schedule is None:
            return None

        keys = list(schedule.versions)
        live = set(
            BuilderDocument.objects.filter(key__in=keys).values_list(
                "key", flat=True
            )
        )
        versions = {
            v.pk: v
            for v in BuilderVersion.objects.filter(
                pk__in=schedule.versions.values()
            )
        }
        issues = [
            Issue(
                path="/documents",
                code="document_gone",
                message=_("%(key)s no longer exists.") % {"key": key},
                document=key,
            )
            for key in keys
            if key not in live or schedule.versions[key] not in versions
        ]
        if not issues:
            for key in keys:
                issues += [
                    Issue(
                        path=issue.path,
                        code=issue.code,
                        message=issue.message,
                        document=key,
                    )
                    for issue in validation.check_references(
                        versions[schedule.versions[key]].refs
                    )
                ]
        previous = published_versions()
        merged = {**previous, **schedule.versions}
        if not issues and merged == previous:
            issues = [
                Issue(
                    path="/documents",
                    code="nothing_to_publish",
                    message=_("Nothing has changed since the last release."),
                )
            ]
        if issues:
            _fail(schedule, issues)
            return None

        release = _create_release(
            schedule.created_by_staff_id, merged, note=schedule.note
        )
        for document in BuilderDocument.objects.select_for_update().filter(
            key__in=keys
        ):
            if document.draft_revision == schedule.revisions.get(document.key):
                document.published_revision = document.draft_revision
                document.save(update_fields=["published_revision"])
        schedule.status = ScheduleStatus.PUBLISHED
        schedule.executed_at = timezone.now()
        schedule.release = release
        schedule.save(
            update_fields=["status", "executed_at", "release", "updated_at"]
        )
        return release.number


def run_due() -> dict[str, int]:
    """The sweep for the store being served: every due schedule, each in
    its own transaction so one failure never holds up the others."""
    due = list(
        BuilderScheduledPublish.objects.filter(
            status=ScheduleStatus.PENDING, scheduled_for__lte=timezone.now()
        )
        .order_by("scheduled_for")
        .values_list("pk", flat=True)
    )
    published = sum(execute(pk) is not None for pk in due)
    return {"due": len(due), "published": published}
