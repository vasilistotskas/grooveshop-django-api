"""Admin views for email template management."""

import logging
from typing import Any

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.template.response import TemplateResponse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods
from django.views.generic import TemplateView

from .forms import EmailPreviewForm
from .preview_service import EmailTemplatePreviewService
from .registry import EmailTemplateRegistry

logger = logging.getLogger(__name__)

# NOTE: access control for every view in this module is applied at the
# URLconf (``core/email/urls.py``) via ``admin.site.admin_view``, which
# runs the tenant-membership gate. Do NOT fall back to
# ``staff_member_required`` here — it only checks the global
# ``is_staff`` flag and would let a merchant read another store's
# orders. On top of that gate, previews render real orders, so both
# views need ``order.view_order``.


def _require_order_access(request: HttpRequest) -> None:
    if not request.user.has_perm("order.view_order"):
        raise PermissionDenied


def _preview_form(
    registry: EmailTemplateRegistry, data=None
) -> EmailPreviewForm:
    return EmailPreviewForm(
        data,
        template_names=[t.name for t in registry.get_all_templates()],
    )


class EmailTemplateManagementView(TemplateView):
    template_name = "admin/email_template_management.html"

    def dispatch(self, request, *args, **kwargs):
        _require_order_access(request)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        # Unfold renders its chrome (sidebar, site menu, environment
        # badge) from the admin site's ``each_context``; a plain
        # TemplateView does not get it.
        context.update(admin.site.each_context(self.request))
        registry = EmailTemplateRegistry()
        context.update(
            {
                "title": _("Email Template Management"),
                "form": _preview_form(registry),
                "templates_by_category": {
                    category: registry.get_by_category(category)
                    for category in registry.get_categories()
                },
            }
        )
        return context


@require_http_methods(["POST"])
def preview_template(request: HttpRequest) -> HttpResponse:
    """The preview panel's fragment, posted by htmx from the page's form."""
    _require_order_access(request)
    form = _preview_form(EmailTemplateRegistry(), request.POST)
    if not form.is_valid():
        return TemplateResponse(
            request,
            "admin/email_templates/preview.html",
            {"error": _("Select a template to preview.")},
        )

    data = form.cleaned_data
    preview = EmailTemplatePreviewService().generate_preview(
        template_name=data["template"],
        order_id=data["order"],
        language=data["language"],
    )
    if preview.error:
        logger.warning(
            "Email preview failed for %s: %s", data["template"], preview.error
        )
    return TemplateResponse(
        request,
        "admin/email_templates/preview.html",
        {
            "error": preview.error,
            "format": data["format"],
            "subject": preview.subject,
            "html": preview.html_content,
            "text": preview.text_content,
        },
    )
