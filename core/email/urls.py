"""URL routing for email template management."""

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
        EmailTemplateManagementView.as_view(),
        name="management",
    ),
    path(
        "preview/",
        preview_template_ajax,
        name="preview",
    ),
    path(
        "template/<str:template_name>/",
        get_template_info,
        name="template_info",
    ),
    path(
        "order/<int:order_id>/",
        get_order_data,
        name="order_data",
    ),
]
