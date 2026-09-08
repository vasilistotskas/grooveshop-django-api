from datetime import timedelta

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db.models.functions import Length
from django.http import FileResponse, Http404
from django.template.defaultfilters import filesizeformat
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from unfold.admin import TabularInline
from unfold.contrib.filters.admin import (
    DropdownFilter,
    FieldTextFilter,
    RangeDateTimeFilter,
)
from unfold.decorators import display

from admin.displays import format_dt, header_two_line, relative_time
from admin.export import ExportModelAdmin
from contact.models import Contact, ContactAttachment, Feedback

FEEDBACK_RATING_VARIANT: dict[str, str] = {
    "5": "success",
    "4": "success",
    "3": "info",
    "2": "warning",
    "1": "danger",
}

CONTACT_PRIORITY_VARIANT: dict[str, str] = {
    "urgent": "danger",
    "high": "warning",
    "medium": "info",
    "low": "success",
}

#: Unfold label colours for a scan verdict. SKIPPED is deliberately
#: neutral rather than green: no engine looked at the file, and the
#: admin must not imply one did.
ATTACHMENT_SCAN_VARIANT: dict[str, str] = {
    "CLEAN": "success",
    "SKIPPED": "info",
    "PENDING": "warning",
    "ERROR": "warning",
    "INFECTED": "danger",
}


class MessageLengthFilter(DropdownFilter):
    title = _("Message Length")
    parameter_name = "message_length"

    def lookups(self, request, model_admin):
        return [
            ("short", _("Short (<100 chars)")),
            ("medium", _("Medium (100-500 chars)")),
            ("long", _("Long (>500 chars)")),
        ]

    def queryset(self, request, queryset):
        if self.value() == "short":
            return queryset.annotate(msg_len=Length("message")).filter(
                msg_len__lt=100
            )
        elif self.value() == "medium":
            return queryset.annotate(msg_len=Length("message")).filter(
                msg_len__range=(100, 500)
            )
        elif self.value() == "long":
            return queryset.annotate(msg_len=Length("message")).filter(
                msg_len__gt=500
            )
        return queryset


class RecentContactFilter(DropdownFilter):
    title = _("Contact Period")
    parameter_name = "contact_period"

    def lookups(self, request, model_admin):
        return [
            ("today", _("Today")),
            ("week", _("This Week")),
            ("month", _("This Month")),
            ("quarter", _("This Quarter")),
        ]

    def queryset(self, request, queryset):
        now = timezone.now()
        if self.value() == "today":
            start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return queryset.filter(created_at__gte=start_date)
        elif self.value() == "week":
            return queryset.filter(created_at__gte=now - timedelta(days=7))
        elif self.value() == "month":
            return queryset.filter(created_at__gte=now - timedelta(days=30))
        elif self.value() == "quarter":
            return queryset.filter(created_at__gte=now - timedelta(days=90))
        return queryset


class ContactAttachmentInline(TabularInline):
    """The files that came with an enquiry, and the way to read them.

    Read-only apart from delete: nothing about an upload is editable -
    the name, size, checksum and verdict are all statements of what
    arrived - and staff CAN remove one, which drops the bytes through
    ``contact.signals.delete_attachment_file``.
    """

    model = ContactAttachment
    extra = 0
    max_num = 0
    can_delete = True
    tab = True
    fields = (
        "download",
        "content_type",
        "size_display",
        "scan_state",
        "created_at",
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        # Uploads arrive through the public endpoint, which pairs each
        # file with a checksum and a scan verdict. A file hand-added
        # here would have neither.
        return False

    @admin.display(description=_("File"))
    def download(self, obj):
        if not obj or not obj.pk:
            return "—"
        if not obj.file:
            return _("%(name)s (bytes deleted)") % {"name": obj.original_name}
        if not obj.is_downloadable():
            return _("%(name)s (withheld)") % {"name": obj.original_name}
        return format_html(
            '<a href="{url}" rel="noopener">{label}</a>',
            url=reverse("admin:contact_attachment_download", args=[obj.uuid]),
            label=obj.original_name,
        )

    @admin.display(description=_("Size"))
    def size_display(self, obj):
        return filesizeformat(obj.size) if obj else "—"

    @display(description=_("Scan"), label=ATTACHMENT_SCAN_VARIANT)
    def scan_state(self, obj):
        return obj.scan_status, obj.get_scan_status_display()


@admin.register(Contact)
class ContactAdmin(ExportModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = True
    list_filter_submit = True
    list_filter_sheet = True

    list_display = [
        "contact_info",
        "enquiry_subject",
        "message_preview",
        "message_stats",
        "contact_timing",
        "priority",
    ]
    list_filter = [
        RecentContactFilter,
        MessageLengthFilter,
        # A text search, not a dropdown: the values are the merchant's
        # own (its form declares them, not a platform enum), so there
        # is no choice list to populate one from.
        ("subject", FieldTextFilter),
        ("created_at", RangeDateTimeFilter),
        ("updated_at", RangeDateTimeFilter),
    ]
    search_fields = ["name", "email", "message", "company", "phone"]
    readonly_fields = (
        "id",
        "uuid",
        "created_at",
        "updated_at",
    )
    list_per_page = 25
    date_hierarchy = "created_at"

    fieldsets = (
        (
            _("Contact Information"),
            {
                "fields": ("name", "email", "company", "phone"),
                "classes": ("wide",),
            },
        ),
        (
            _("Message"),
            {
                "fields": ("subject", "message"),
                "classes": ("wide",),
            },
        ),
        (
            _("System Information"),
            {
                "fields": ("id", "uuid", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    inlines = [ContactAttachmentInline]

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "attachment/<uuid:attachment_uuid>/download/",
                self.admin_site.admin_view(self.attachment_download_view),
                name="contact_attachment_download",
            ),
        ]
        return custom + urls

    def attachment_download_view(self, request, attachment_uuid):
        """The ONLY reader of an attachment's bytes.

        There is no public URL, signed or otherwise: the file lives in
        a private per-tenant tree no web server serves
        (``contact.storage``), and this view is what an authorised
        reader goes through instead. Three things it insists on:

        * ``admin_view`` plus the model's ``view`` permission - the
          admin login is membership-gated per tenant
          (``admin.admin.MyAdminSite.has_permission``), and reading an
          anonymous upload is staff activity like any other, so it is
          not enough to merely be logged in somewhere.
        * ``is_downloadable()`` - PENDING and ERROR are withheld
          because a verdict that never arrived is not a clean one, and
          INFECTED never has bytes to serve.
        * ``application/octet-stream`` with ``as_attachment`` and
          ``nosniff`` - the stored type is recorded on the row for the
          operator, but handing it back would invite a browser to
          render an anonymous stranger's file in a staff session.
        """
        opts = ContactAttachment._meta
        if not request.user.has_perm(
            f"{opts.app_label}.view_{opts.model_name}"
        ):
            raise PermissionDenied
        attachment = ContactAttachment.objects.filter(
            uuid=attachment_uuid
        ).first()
        if attachment is None or not attachment.file:
            raise Http404(_("Attachment not found."))
        if not attachment.is_downloadable():
            raise Http404(
                _("This attachment is withheld pending its scan result.")
            )
        response = FileResponse(
            attachment.file.open("rb"),
            content_type="application/octet-stream",
            as_attachment=True,
            filename=attachment.original_name,
        )
        response["X-Content-Type-Options"] = "nosniff"
        return response

    def get_ordering(self, request):
        return ["-created_at", "name"]

    @display(description=_("Subject"), ordering="subject")
    def enquiry_subject(self, obj):
        """What the sender said the enquiry is about, plus who they are.

        Both halves are optional and usually absent — the platform's
        own form asks for neither — so this renders an em dash rather
        than an empty cell.
        """
        return (
            " · ".join(part for part in (obj.subject, obj.company) if part)
            or "—"
        )

    @display(description=_("Contact"), header=True, ordering="name")
    def contact_info(self, obj):
        is_suspicious = (
            not obj.email
            or "@" not in obj.email
            or "." not in obj.email.split("@")[-1]
        )
        email = str(obj.email or _("(no email)"))
        if is_suspicious:
            email = _("%(email)s (invalid)") % {"email": email}
        return header_two_line(obj.name, email)

    @admin.display(description=_("Message"))
    def message_preview(self, obj):
        full = (obj.message or "").replace("\n", " ").replace("\r", " ")
        return full[:100] + ("..." if len(full) > 100 else "")

    @admin.display(description=_("Message Stats"))
    def message_stats(self, obj):
        msg = obj.message or ""
        return _("%(chars)d chars, %(words)d words, %(lines)d lines") % {
            "chars": len(msg),
            "words": len(msg.split()),
            "lines": msg.count("\n") + 1,
        }

    @admin.display(description=_("Timing"), ordering="created_at")
    def contact_timing(self, obj):
        return f"{format_dt(obj.created_at, fmt='%d/%m/%Y')} ({relative_time(obj.created_at)})"

    @display(
        description=_("Priority"),
        label=CONTACT_PRIORITY_VARIANT,
        ordering="created_at",
    )
    def priority(self, obj):
        now = timezone.now()
        diff = now - obj.created_at
        length = len(obj.message or "")

        if diff < timedelta(hours=2):
            return "urgent", _("Urgent")
        if length > 500:
            return "high", _("High")
        if diff < timedelta(days=1):
            return "medium", _("Medium")
        return "low", _("Low")


@admin.register(Feedback)
class FeedbackAdmin(ExportModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = True
    list_filter_submit = True
    list_filter_sheet = True

    list_display = [
        "submitter_info",
        "rating_display",
        "category",
        "message_preview",
        "feedback_timing",
    ]
    list_filter = [
        "category",
        "rating",
        ("created_at", RangeDateTimeFilter),
    ]
    search_fields = ["name", "email", "message"]
    readonly_fields = (
        "id",
        "uuid",
        "created_at",
        "updated_at",
    )
    list_per_page = 25
    date_hierarchy = "created_at"

    fieldsets = (
        (
            _("Submitter"),
            {
                "fields": ("name", "email"),
                "classes": ("wide",),
            },
        ),
        (
            _("Feedback"),
            {
                "fields": ("rating", "category", "message"),
                "classes": ("wide",),
            },
        ),
        (
            _("System Information"),
            {
                "fields": ("id", "uuid", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    def get_ordering(self, request):
        return ["-created_at"]

    @display(description=_("Submitter"), header=True, ordering="name")
    def submitter_info(self, obj):
        name = obj.name or str(_("Anonymous"))
        email = obj.email or str(_("(no email)"))
        return header_two_line(name, email)

    @display(
        description=_("Rating"),
        label=FEEDBACK_RATING_VARIANT,
        ordering="rating",
    )
    def rating_display(self, obj):
        return str(obj.rating), f"{obj.rating}★"

    @admin.display(description=_("Message"))
    def message_preview(self, obj):
        full = (obj.message or "").replace("\n", " ").replace("\r", " ")
        return full[:100] + ("..." if len(full) > 100 else "")

    @admin.display(description=_("Timing"), ordering="created_at")
    def feedback_timing(self, obj):
        return f"{format_dt(obj.created_at, fmt='%d/%m/%Y')} ({relative_time(obj.created_at)})"
