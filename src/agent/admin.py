from __future__ import annotations

from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _
from unfold.decorators import action

from admin.base import BaseModelAdmin
from admin.mixins import IsSuperuserOnlyModelAdmin
from agent.models import AgentOrderEvent
from agent.outbox import retry_now


@admin.register(AgentOrderEvent)
class AgentOrderEventAdmin(IsSuperuserOnlyModelAdmin, BaseModelAdmin):
    """The order events owed to the agent gateway, read-only; a failed or
    waiting one can be sent again now."""

    list_display = (
        "id",
        "order",
        "event_type",
        "status",
        "attempts",
        "occurred_at",
        "next_attempt_at",
        "finished_at",
        "last_error",
    )
    list_filter = ("status", "event_type", "occurred_at")
    search_fields = ("=order__id", "=order__uuid")
    date_hierarchy = "occurred_at"
    list_select_related = ("order",)
    readonly_fields = (
        "id",
        "order",
        "event_type",
        "occurred_at",
        "status",
        "attempts",
        "next_attempt_at",
        "last_attempt_at",
        "finished_at",
        "last_error",
    )
    actions = ("retry_events",)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    @action(
        description=_("Send again now"), permissions=["change"], icon="replay"
    )
    def retry_events(
        self, request: HttpRequest, queryset: QuerySet[AgentOrderEvent]
    ) -> None:
        queued = retry_now(queryset)
        self.message_user(
            request,
            _("%(count)d events queued; delivered events are not sent again.")
            % {"count": queued},
            messages.SUCCESS,
        )
