"""The store admin's email pages.

``EmailSwitchAdmin`` owns the page, not a changelist: ``changelist_view`` is
the status page (every email the store may switch, with what it sent and how
many failed, filtered by audience, text and state), ``type_view`` the page of
one email (its preview, a test send and its last sends), ``toggle_view`` the
htmx endpoint a switch talks to and ``test_send_view`` the Send test. Every
switch write goes through ``emails.services.set_switch`` (permission,
validation, history with the actor, the store's cache generation); the model
has no add or change form, because a form would bypass the rule that a
required email stays on.

``EmailLogAdmin`` and ``EmailSuppressionAdmin`` are the read-only log and the
suppression list. Neither ever shows an address: a row holds a hash and a
masked form, and an exact address typed in the search box is hashed here and
matched on the hash.
"""

from __future__ import annotations

import logging

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.decorators import method_decorator
from django.utils.translation import get_language, gettext
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods
from django_tenants.utils import get_public_schema_name
from unfold.admin import TabularInline
from unfold.contrib.filters.admin import (
    ChoicesDropdownFilter,
    DropdownFilter,
    RangeDateTimeFilter,
)
from unfold.decorators import display
from unfold.widgets import (
    UnfoldAdminSelectWidget,
    UnfoldAdminTextInputWidget,
    UnfoldBooleanSwitchWidget,
)

from admin.base import BaseModelAdmin
from admin.displays import label_cell, relative_time
from emails import figures, switches, testsend
from emails.models import (
    EmailEvent,
    EmailLog,
    EmailSuppression,
    EmailSwitch,
)
from emails.optout import address_hash
from emails.preview import EmailPreviewForm, build_preview
from emails.registry import UnknownEmailType, registry
from emails.services import CHANGE_PERMISSION, set_switch
from emails.status import Status
from emails.types import EmailType, Owner
from order.emails import NothingToPreview

logger = logging.getLogger(__name__)

#: The order permission that lets a preview or a test render a real order.
ORDER_PERMISSION = "order.view_order"

#: How many of a type's latest sends its page lists.
RECENT_SENDS = 20

STATUS_VARIANTS = {
    Status.SENT: "success",
    Status.DELIVERED: "success",
    Status.QUEUED: "info",
    Status.DELAYED: "warning",
    Status.FAILED: "danger",
    Status.BOUNCED: "danger",
    Status.COMPLAINED: "danger",
    Status.PROVIDER_SUPPRESSED: "danger",
}


def status_variant(status: str) -> str:
    """Success once the provider has it, danger when it did not arrive,
    the neutral colour for a skip (nothing was meant to be sent)."""
    return STATUS_VARIANTS.get(Status(status), "default")


def type_title(type_key: str) -> str:
    """The email's title, or its key for a type that no longer exists."""
    try:
        return str(registry.get(type_key).title)
    except UnknownEmailType:
        return type_key


class EmailPageForm(forms.Form):
    """The status page's filters; a GET form, so a filtered view is a URL."""

    tab = forms.ChoiceField(
        choices=[(switches.CUSTOMERS, ""), (switches.TEAM, "")],
        required=False,
        widget=forms.HiddenInput,
    )
    q = forms.CharField(
        label=_("Search"),
        required=False,
        widget=UnfoldAdminTextInputWidget(
            attrs={"type": "search", "placeholder": _("Search emails...")}
        ),
    )
    status = forms.ChoiceField(
        label=_("Show"),
        required=False,
        choices=[
            ("", _("All emails")),
            (switches.Condition.ON, _("On")),
            (switches.Condition.OFF, _("Off")),
            (switches.Condition.FAILING, _("Failing")),
        ],
        widget=UnfoldAdminSelectWidget,
    )

    def __init__(self, *args, platform: bool = False, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if platform:
            # The platform's emails are all required and ungated: none is
            # "off", and nothing is switched.
            self.fields["status"].choices = [
                ("", _("All emails")),
                (switches.Condition.FAILING, _("Failing")),
            ]


class HashedAddressSearch:
    """Search by an exact address, hashed here and matched on the hash. Any
    other text searches the plain ``search_fields``."""

    address_hash_field = "recipient_hash"

    def get_search_results(self, request, queryset, search_term):
        if "@" in search_term:
            return queryset.filter(
                **{self.address_hash_field: address_hash(search_term)}
            ), False
        return super().get_search_results(request, queryset, search_term)


@admin.register(EmailSwitch)
class EmailSwitchAdmin(BaseModelAdmin):
    # The page replaces the changelist; nothing of ModelAdmin's list is used.
    list_display = ("type_key",)
    #: Whose emails the page lists. ``PlatformEmailSwitchAdmin`` is the
    #: platform's.
    owner = Owner.STORE
    #: The sentence under the page's title.
    intro = _(
        "Every email your store sends. Switch one off and your store stops "
        "sending it, and only that one. Emails that a customer or the law "
        "needs, such as an order confirmation or a password reset, cannot be "
        "switched off."
    )

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
            path(
                "type/<str:key>/",
                self.admin_site.admin_view(self.type_view),
                name=f"{info[0]}_{info[1]}_type",
            ),
            path(
                "test-send/",
                self.admin_site.admin_view(self.test_send_view),
                name=f"{info[0]}_{info[1]}_test_send",
            ),
            path(
                "preview/render/",
                self.admin_site.admin_view(self.preview_render_view),
                name=f"{info[0]}_{info[1]}_preview_render",
            ),
            *super().get_urls(),
        ]

    def _url(self, name: str, *args) -> str:
        return self._reverse(
            f"{self.opts.app_label}_{self.opts.model_name}_{name}", *args
        )

    def _reverse(self, name: str, *args) -> str:
        """*name* on the site that serves this admin: ``admin`` for a store,
        ``platform_admin`` for the control plane."""
        return reverse(f"{self.admin_site.name}:{name}", args=args)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        return redirect(self._url("changelist"))

    # ----------------------------------------------------------- the status

    def _links(self, request: HttpRequest) -> dict[str, str | None]:
        """The pages beside this one, for those who may open them."""
        user = request.user
        return {
            "log_url": self._reverse("emails_emaillog_changelist")
            if user.has_perm("emails.view_emaillog")
            else None,
            "suppressions_url": self._reverse(
                "emails_emailsuppression_changelist"
            )
            if user.has_perm("emails.view_emailsuppression")
            else None,
        }

    def changelist_view(self, request, extra_context=None):
        if not self.has_view_permission(request):
            raise PermissionDenied
        platform = self.owner is Owner.PLATFORM
        form = EmailPageForm(request.GET, platform=platform)
        data = form.cleaned_data if form.is_valid() else {}
        tab = data.get("tab") or switches.CUSTOMERS
        # The tab is chosen by a link, so the form's own value is the one
        # the link carries.
        form = EmailPageForm(
            platform=platform,
            initial={
                "tab": tab,
                "q": data.get("q", ""),
                "status": data.get("status", ""),
            },
        )
        query = {
            key: value
            for key, value in (
                ("q", data.get("q", "")),
                ("status", data.get("status", "")),
            )
            if value
        }
        page = self._url("changelist")
        return TemplateResponse(
            request,
            "admin/emails/switches.html",
            {
                **self.admin_site.each_context(request),
                "title": _("Emails"),
                "intro": self.intro,
                "opts": self.opts,
                "form": form,
                "tab": tab,
                # A store's page has two audiences; the platform's groups
                # its emails by audience instead.
                "tabs": []
                if platform
                else [
                    {
                        "label": label,
                        "active": key == tab,
                        "url": f"{page}?{self._query({**query, 'tab': key})}",
                    }
                    for key, label in (
                        (switches.CUSTOMERS, _("To customers")),
                        (switches.TEAM, _("To your team")),
                    )
                ],
                "filtered": bool(query),
                "groups": [
                    (group, [self._row(request, row) for row in group.rows])
                    for group in switches.switch_groups(
                        owner=self.owner,
                        tab=tab,
                        query=data.get("q", ""),
                        condition=data.get("status", ""),
                        figures=figures.type_figures.get(),
                    )
                ],
                **self._links(request),
            },
        )

    @staticmethod
    def _query(params: dict[str, str]) -> str:
        from urllib.parse import urlencode

        return urlencode(params)

    def _control(self, request: HttpRequest, row: switches.SwitchRow) -> str:
        """Unfold's own switch, disabled for a viewer who may not change it."""
        attrs = {"id": f"switch-{row.email_type.key}"}
        if not request.user.has_perm(CHANGE_PERMISSION):
            attrs["disabled"] = True
        return UnfoldBooleanSwitchWidget(attrs=attrs).render(
            "enabled", row.enabled
        )

    def _row(self, request: HttpRequest, row: switches.SwitchRow) -> dict:
        key = row.email_type.key
        return {
            "row": row,
            "control": self._control(request, row) if row.switchable else "",
            "last_sent": relative_time(row.figures.last_sent_at)
            if row.figures.last_sent_at
            else "",
            "page_url": self._url("type", key),
            "toggle_url": self._url("toggle"),
            "test_send_url": self._url("test_send"),
            "failing_url": (
                self._reverse("emails_emaillog_changelist")
                + "?"
                + self._query({"type_key": key, "failing": "30"})
            )
            if request.user.has_perm("emails.view_emaillog")
            else None,
            "can_test": request.user.has_perm(CHANGE_PERMISSION),
        }

    # ------------------------------------------------------------ one email

    def _may_pick_orders(self, request: HttpRequest) -> bool:
        """A store's viewer with the order permission may preview or test
        with a real order; the platform has no orders (they live in the
        stores' schemas), whoever asks."""
        return self.owner is Owner.STORE and request.user.has_perm(
            ORDER_PERMISSION
        )

    def _type_or_404(self, key: str) -> EmailType:
        try:
            email_type = registry.get(key)
        except UnknownEmailType:
            raise Http404 from None
        if email_type.owner is not self.owner:
            raise Http404
        return email_type

    def type_view(self, request: HttpRequest, key: str) -> HttpResponse:
        """One email: its state, its preview, a test send and its last
        sends with what the provider reported."""
        if not self.has_view_permission(request):
            raise PermissionDenied
        email_type = self._type_or_404(key)
        enabled = not EmailSwitch.objects.filter(type_key=key).exists()
        row = self._row(
            request,
            switches.row_for(
                email_type,
                enabled=enabled,
                figures=figures.type_figures.get().get(key),
            ),
        )
        form = EmailPreviewForm(
            initial={"type_key": key, "language": get_language()},
            owner=self.owner,
            with_orders=self._may_pick_orders(request),
        )
        recent = EmailLog.objects.filter(type_key=key).prefetch_related(
            "events"
        )[:RECENT_SENDS]
        return TemplateResponse(
            request,
            "admin/emails/type.html",
            {
                **self.admin_site.each_context(request),
                "title": email_type.title,
                "opts": self.opts,
                "email_type": email_type,
                "item": row,
                "form": form,
                "preview_render_url": self._url("preview_render"),
                "back_url": self._url("changelist"),
                "table": self._recent_table(recent),
                "can_test": row["can_test"],
                "test_send_attrs": {
                    "formaction": self._url("test_send"),
                    "formmethod": "post",
                },
                **self._links(request),
            },
        )

    @staticmethod
    def _recent_table(logs) -> dict:
        rows = []
        for entry in logs:
            timeline = ", ".join(
                event.type.removeprefix("email.")
                for event in entry.events.all()
            )
            when = gettext("%(ago)s ago") % {
                "ago": relative_time(entry.created_at)
            }
            if entry.is_test:
                when = f"{when} · {gettext('test')}"
            rows.append(
                [
                    when,
                    entry.recipient_masked or "—",
                    label_cell(
                        Status(entry.status).label,
                        status_variant(entry.status),
                    ),
                    timeline or "—",
                ]
            )
        return {
            "headers": [
                _("Sent"),
                _("To"),
                _("Status"),
                _("What the provider reported"),
            ],
            "rows": rows,
        }

    @method_decorator(require_http_methods(["POST"]))
    def preview_render_view(self, request: HttpRequest) -> HttpResponse:
        """The preview panel's fragment, posted by htmx from the form."""
        if not self.has_view_permission(request):
            raise PermissionDenied
        form = EmailPreviewForm(
            request.POST,
            owner=self.owner,
            with_orders=self._may_pick_orders(request),
        )
        if not form.is_valid():
            return TemplateResponse(
                request,
                "admin/emails/_preview.html",
                {"error": _("Select an email to preview.")},
            )
        data = form.cleaned_data
        preview = build_preview(
            registry.get(data["type_key"]),
            order_id=data.get("order"),
            language=data["language"],
        )
        return TemplateResponse(
            request,
            "admin/emails/_preview.html",
            {
                "error": preview.error,
                "format": data["format"],
                "subject": preview.subject,
                "html": preview.html,
                "text": preview.text,
            },
        )

    @method_decorator(require_http_methods(["POST"]))
    def test_send_view(self, request: HttpRequest) -> HttpResponse:
        """Send one email to the signed-in staff member, and nobody else.

        The address is the account's, never a posted one: a ``to`` field in
        the request is not read.
        """
        if not request.user.has_perm(CHANGE_PERMISSION):
            raise PermissionDenied
        email_type = self._type_or_404(request.POST.get("type_key", ""))
        language = request.POST.get("language", "")
        if language not in dict(settings.LANGUAGES):
            language = get_language()
        order = request.POST.get("order", "")
        order_id = (
            int(order)
            if order.isdigit() and self._may_pick_orders(request)
            else None
        )
        back = self._url("type", email_type.key)
        try:
            testsend.send_test(
                request.user,
                email_type,
                order_id=order_id,
                language=language,
            )
        except testsend.Throttled:
            messages.error(
                request,
                gettext(
                    "You have used all your test emails for this hour. "
                    "Try again later."
                ),
            )
        except testsend.NoAddress:
            messages.error(
                request,
                gettext("Your account has no email address to send it to."),
            )
        except NothingToPreview as nothing:
            messages.error(request, str(nothing))
        except Exception:
            logger.exception(
                "Test send of %s failed",
                email_type.key,
                extra={"email_type": email_type.key},
            )
            messages.error(
                request,
                gettext(
                    "The test email could not be sent. The email log has the "
                    "reason."
                ),
            )
        else:
            messages.success(
                request,
                gettext("A test of “%(title)s” was sent to %(address)s.")
                % {"title": email_type.title, "address": request.user.email},
            )
        return redirect(back)

    # -------------------------------------------------------------- endpoint

    @method_decorator(require_http_methods(["POST"]))
    def toggle_view(self, request: HttpRequest) -> HttpResponse:
        """Switch one email on or off, and answer with its row."""
        if not self.has_view_permission(request):
            raise PermissionDenied
        key = request.POST.get("type_key", "")
        enabled = request.POST.get("enabled") == "on"
        email_type = self._type_or_404(key)
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
                    request,
                    switches.row_for(
                        email_type,
                        enabled=enabled,
                        figures=figures.type_figures.get().get(email_type.key),
                    ),
                ),
                "opts": self.opts,
            },
        )


class PlatformEmailSwitchAdmin(EmailSwitchAdmin):
    """The control plane's emails page: the platform's own mail (billing
    notices, the staff password reset, the health and backup alerts), sent
    from ``public``. Every one is required, so there is nothing to switch
    (the toggle answers 404 for them, as for any email of the other
    owner), and there are no orders to preview or test with; the figures,
    the preview, Send test, the log and the suppression list are the
    store page's, over the ``public`` schema."""

    owner = Owner.PLATFORM
    intro = _(
        "The emails the platform itself sends: billing notices to the "
        "stores' owners, the staff password reset and the alerts to the "
        "platform's operators. All of them are required, so none can be "
        "switched off."
    )

    def toggle_view(self, request: HttpRequest) -> HttpResponse:
        raise Http404


# ------------------------------------------------------------------- the log


def serving_owner() -> Owner:
    """Whose emails the schema in use holds: the platform's in ``public``,
    a store's in a store's schema. The log and the suppression list are one
    admin class on both sites, so they ask here."""
    if connection.schema_name == get_public_schema_name():
        return Owner.PLATFORM
    return Owner.STORE


class EmailTypeFilter(DropdownFilter):
    title = _("Email")
    parameter_name = "type_key"

    def lookups(self, request, model_admin):
        return [
            (email_type.key, email_type.title)
            for email_type in registry.types_for(serving_owner())
        ]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(type_key=self.value())
        return queryset


class FailingFilter(DropdownFilter):
    """What the page's failure count and the dashboard count, as a list."""

    title = _("Failing")
    parameter_name = "failing"

    def lookups(self, request, model_admin):
        return [
            ("7", _("In the last 7 days")),
            ("30", _("In the last 30 days")),
        ]

    def queryset(self, request, queryset):
        days = self.value()
        if days in ("7", "30"):
            return queryset.filter(figures.failing_since(int(days)))
        return queryset


class EmailEventInline(TabularInline):
    model = EmailEvent
    extra = 0
    can_delete = False
    fields = ("occurred_at", "type", "detail")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


@admin.register(EmailLog)
class EmailLogAdmin(HashedAddressSearch, BaseModelAdmin):
    """Every email the store sent, skipped or failed to send; read-only."""

    # The email and its status first: on a phone the table scrolls sideways,
    # and those are the two columns a reader came for.
    list_display = (
        "email",
        "state",
        "recipient_masked",
        "created_at",
        "subject",
        "provider",
    )
    list_filter = (
        EmailTypeFilter,
        ("status", ChoicesDropdownFilter),
        FailingFilter,
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("subject",)
    search_help_text = _(
        "Type an exact email address to find the emails sent to it."
    )
    ordering = ("-created_at",)
    readonly_fields = (
        "created_at",
        "email",
        "recipient_masked",
        "state",
        "reason",
        "language",
        "subject",
        "provider",
        "provider_message_id",
        "error",
        "sent_at",
        "last_event_at",
        "is_test",
    )
    fields = readonly_fields
    inlines = [EmailEventInline]

    @display(description=_("Email"), ordering="type_key")
    def email(self, obj: EmailLog) -> str:
        return type_title(obj.type_key)

    @display(description=_("Status"), ordering="status")
    def state(self, obj: EmailLog) -> str:
        return label_cell(Status(obj.status).label, status_variant(obj.status))

    def has_view_permission(self, request, obj=None) -> bool:
        return request.user.has_perm("emails.view_emaillog")

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(EmailSuppression)
class EmailSuppressionAdmin(HashedAddressSearch, BaseModelAdmin):
    """The addresses the store (or the platform) will not write to again.

    Removing a row is the one write, with the standard delete confirmation
    (a dialog that says the address must also leave Resend's own list) and
    the admin log naming the actor.
    """

    address_hash_field = "address_hash"
    list_display = ("recipient_masked", "reason", "scope", "created_at")
    list_filter = (
        ("reason", ChoicesDropdownFilter),
        ("scope", ChoicesDropdownFilter),
    )
    search_fields = ("recipient_masked",)
    search_help_text = _(
        "Type an exact email address to find whether it is suppressed."
    )
    ordering = ("-created_at",)
    readonly_fields = (
        "recipient_masked",
        "reason",
        "scope",
        "source_log",
        "created_at",
    )
    fields = readonly_fields

    def has_view_permission(self, request, obj=None) -> bool:
        return request.user.has_perm("emails.view_emailsuppression")

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return request.user.has_perm("emails.delete_emailsuppression")
