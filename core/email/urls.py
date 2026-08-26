"""URL routing for email template management.

Every view is wrapped in ``admin.site.admin_view`` so it inherits
``MyAdminSite.has_permission`` — the platform-staff session check PLUS
the per-tenant ``UserTenantMembership`` check. ``staff_member_required``
alone is NOT enough here: ``is_staff`` is a global flag on the shared
``UserAccount``, so a merchant staffer of store A would otherwise read
store B's orders (this page lists recent orders and renders their
emails). The mount point is storefront-only (see ``core/urls.py``);
these views query ``Order``, which lives in TENANT_APPS.
"""

from django.contrib import admin
from django.urls import path

from .admin_views import (
    EmailTemplateManagementView,
    preview_template_ajax,
    get_template_info,
    get_order_data,
)

app_name = "email_templates"

urlpatterns = [
    path(
        "management/",
        admin.site.admin_view(EmailTemplateManagementView.as_view()),
        name="management",
    ),
    path(
        "preview/",
        admin.site.admin_view(preview_template_ajax),
        name="preview",
    ),
    path(
        "template/<str:template_name>/",
        admin.site.admin_view(get_template_info),
        name="template_info",
    ),
    path(
        "order/<int:order_id>/",
        admin.site.admin_view(get_order_data),
        name="order_data",
    ),
]
