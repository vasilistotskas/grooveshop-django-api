"""Read the schedule, switch an entry off or on, run one by hand.

``settings.CELERY_BEAT_SCHEDULE`` owns everything else, so nothing else here
can be edited: to change when a job runs, change the settings and deploy.
"""

from typing import Any

from django.conf import settings
from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from django_tenants.utils import get_public_schema_name
from unfold.decorators import display

from admin.base import BaseModelAdmin
from core.celery import celery_app
from scheduler.models import PeriodicTask


def _declared(task: PeriodicTask) -> dict[str, Any]:
    """Its entry, empty for what the scheduler installs itself."""
    return settings.CELERY_BEAT_SCHEDULE.get(task.name, {})


@admin.register(PeriodicTask)
class PeriodicTaskAdmin(BaseModelAdmin):
    list_display = (
        "name",
        "task",
        "schedule",
        "is_enabled",
        "last_run_at",
        "total_run_count",
    )
    list_filter = ("enabled",)
    search_fields = ("name",)
    fields = (
        "name",
        "task",
        "schedule",
        "is_enabled",
        "resumed_at",
        "last_run_at",
        "total_run_count",
    )
    actions = ("switch_on", "switch_off", "run_now")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: PeriodicTask | None = None
    ) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: PeriodicTask | None = None
    ) -> bool:
        return False

    def has_operate_permission(self, request: HttpRequest) -> bool:
        return request.user.has_perm(
            f"{self.opts.app_label}.change_{self.opts.model_name}"
        )

    @display(description=_("Task"))
    def task(self, obj: PeriodicTask) -> str:
        return _declared(obj).get("task", obj.name)

    @display(description=_("Schedule"))
    def schedule(self, obj: PeriodicTask) -> str:
        schedule = _declared(obj).get("schedule")
        if schedule is None:
            return "—"
        return (
            repr(schedule)
            .removeprefix("<crontab: ")
            .removesuffix(" (m/h/dM/MY/d)>")
        )

    @display(description=_("Enabled"), boolean=True, ordering="enabled")
    def is_enabled(self, obj: PeriodicTask) -> bool:
        return obj.enabled

    @admin.action(description=_("Switch on"), permissions=["operate"])
    def switch_on(
        self, request: HttpRequest, queryset: QuerySet[PeriodicTask]
    ) -> None:
        self._log_each(request, queryset, "Switched on")
        count = queryset.switch_on()
        self.message_user(
            request,
            ngettext(
                "%(count)d periodic task switched on.",
                "%(count)d periodic tasks switched on.",
                count,
            )
            % {"count": count},
        )

    @admin.action(description=_("Switch off"), permissions=["operate"])
    def switch_off(
        self, request: HttpRequest, queryset: QuerySet[PeriodicTask]
    ) -> None:
        self._log_each(request, queryset, "Switched off")
        count = queryset.switch_off()
        self.message_user(
            request,
            ngettext(
                "%(count)d periodic task switched off.",
                "%(count)d periodic tasks switched off.",
                count,
            )
            % {"count": count},
        )

    @admin.action(description=_("Run now"), permissions=["operate"])
    def run_now(
        self, request: HttpRequest, queryset: QuerySet[PeriodicTask]
    ) -> None:
        """Queue each one as beat would, in ``public``.

        By name rather than through the task object: importing every task
        module from a web request runs Celery's Django system checks, and
        dj-stripe's aborts the admin's transaction on the platform host.
        """
        sent = 0
        for task in queryset:
            entry = _declared(task)
            if not entry:
                self.message_user(
                    request,
                    _("%(name)s is not declared in the schedule.")
                    % {"name": task.name},
                    level=messages.ERROR,
                )
                continue
            options = entry.get("options", {})
            celery_app.send_task(
                entry["task"],
                args=entry.get("args", ()),
                kwargs=entry.get("kwargs", {}),
                ignore_result=celery_app.conf.task_ignore_result,
                **{
                    **options,
                    "headers": {
                        **options.get("headers", {}),
                        "periodic_task_name": task.name,
                        "_schema_name": get_public_schema_name(),
                    },
                },
            )
            self.log_change(request, task, "Ran by hand")
            sent += 1
        if sent:
            self.message_user(
                request,
                ngettext(
                    "%(count)d periodic task queued.",
                    "%(count)d periodic tasks queued.",
                    sent,
                )
                % {"count": sent},
            )

    def _log_each(
        self,
        request: HttpRequest,
        queryset: QuerySet[PeriodicTask],
        message: str,
    ) -> None:
        for task in queryset:
            self.log_change(request, task, message)
