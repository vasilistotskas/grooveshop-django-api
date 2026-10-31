"""The store admin's email page.

One dedicated page, not a changelist: ``EmailSwitchAdmin.changelist_view``
lists every email the store may switch, by area, and ``toggle_view`` is the
small htmx endpoint a switch talks to. Every write goes through
``emails.services.set_switch`` (permission, validation, history with the
actor, the store's cache generation); the model has no add or change form,
because a form would bypass the rule that a required email stays on.
"""

from __future__ import annotations

from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods
from unfold.widgets import UnfoldBooleanSwitchWidget

from admin.base import BaseModelAdmin
from emails import switches
from emails.models import EmailSwitch
from emails.registry import UnknownEmailType, registry
from emails.services import CHANGE_PERMISSION, set_switch
from emails.types import Owner


@admin.register(EmailSwitch)
class EmailSwitchAdmin(BaseModelAdmin):
    # The page replaces the changelist; nothing of ModelAdmin's list is used.
    list_display = ("type_key",)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    def has_view_permission(self, request, obj=None) -> bool:
        return request.user.has_perm("emails.view_emailswitch")

    # ------------------------------------------------------------------ urls

    def get_urls(self):
        info = self.opts.app_label, self.opts.model_name
        return [
            path(
                "toggle/",
                self.admin_site.admin_view(self.toggle_view),
                name=f"{info[0]}_{info[1]}_toggle",
            ),
            *super().get_urls(),
        ]

    def _url(self, name: str) -> str:
        return reverse(
            f"admin:{self.opts.app_label}_{self.opts.model_name}_{name}"
        )

    def change_view(self, request, object_id, form_url="", extra_context=None):
        return redirect(self._url("changelist"))

    # ------------------------------------------------------------- the page

    def changelist_view(self, request, extra_context=None):
        if not self.has_view_permission(request):
            raise PermissionDenied
        return TemplateResponse(
            request,
            "admin/emails/switches.html",
            {
                **self.admin_site.each_context(request),
                "title": _("Emails"),
                "opts": self.opts,
                "groups": [
                    (group, [self._row(request, row) for row in group.rows])
                    for group in switches.switch_groups()
                ],
            },
        )

    def _control(self, request: HttpRequest, row: switches.SwitchRow) -> str:
        """Unfold's own switch, disabled for a viewer who may not change it."""
        attrs = {"id": f"switch-{row.email_type.key}"}
        if not request.user.has_perm(CHANGE_PERMISSION):
            attrs["disabled"] = True
        return UnfoldBooleanSwitchWidget(attrs=attrs).render(
            "enabled", row.enabled
        )

    def _row(self, request: HttpRequest, row: switches.SwitchRow) -> dict:
        return {
            "row": row,
            "control": self._control(request, row) if row.switchable else "",
        }

    # -------------------------------------------------------------- endpoint

    @method_decorator(require_http_methods(["POST"]))
    def toggle_view(self, request: HttpRequest) -> HttpResponse:
        """Switch one email on or off, and answer with its row."""
        if not self.has_view_permission(request):
            raise PermissionDenied
        key = request.POST.get("type_key", "")
        enabled = request.POST.get("enabled") == "on"
        try:
            email_type = registry.get(key)
        except UnknownEmailType:
            raise Http404 from None
        if email_type.owner is not Owner.STORE:
            raise Http404
        try:
            set_switch(request.user, key, enabled=enabled)
        except ValidationError as error:
            # A required email has no switch on the page: only a crafted
            # request gets here.
            return HttpResponse(" ".join(error.messages), status=400)
        return self._row_response(request, email_type)

    def _row_response(self, request: HttpRequest, email_type) -> HttpResponse:
        enabled = not EmailSwitch.objects.filter(
            type_key=email_type.key
        ).exists()
        return TemplateResponse(
            request,
            "admin/emails/_row.html",
            {
                **self._row(
                    request, switches.row_for(email_type, enabled=enabled)
                ),
                "opts": self.opts,
            },
        )
