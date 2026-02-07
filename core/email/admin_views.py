"""Admin views for email template management."""

import json
from typing import Any

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpRequest, JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_http_methods
from django.views.generic import TemplateView

from order.models import Order
from .preview_service import EmailTemplatePreviewService


@method_decorator(staff_member_required, name="dispatch")
class EmailTemplateManagementView(TemplateView):
    """
    Main admin view for email template management.
    """

    template_name = "admin/email_template_management.html"

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        """
        Get context data for the template.

        Returns:
            Context dictionary
        """
        context = super().get_context_data(**kwargs)

        from .registry import EmailTemplateRegistry

        # Get all templates grouped by category
        registry = EmailTemplateRegistry()
        templates = registry.get_all_templates()
        categories = registry.get_categories()

        # Group templates by category
        templates_by_category = {}
        for category in categories:
            templates_by_category[category] = registry.get_by_category(category)

        # Get recent orders for real data testing
        recent_orders = Order.objects.order_by("-created_at")[:10].values(
            "id",
            "first_name",
            "last_name",
            "status",
            "created_at",
            "paid_amount",
        )

        # Get available languages from Django settings
        available_languages = [
            {"code": code, "name": str(name)}
            for code, name in settings.LANGUAGES
        ]

        context.update(
            {
                "title": "Email Template Management",
                "templates": templates,
                "templates_by_category": templates_by_category,
                "categories": categories,
                "recent_orders": list(recent_orders),
                "available_languages": available_languages,
                "site_title": "GrooveShop",
                "site_header": "GrooveShop Administration",
                "has_permission": True,
            }
        )

        return context


@staff_member_required
@require_http_methods(["POST"])
def preview_template_ajax(request: HttpRequest) -> JsonResponse:
    """
    AJAX endpoint for template preview.

    Args:
        request: HTTP request with JSON data

    Returns:
        JSON response with preview data
    """
    try:
        data = json.loads(request.body)
        template_name = data.get("template_name")
        order_id = data.get("order_id")
        language = data.get("language", "el")
        format_type = data.get("format_type", "html")

        if not template_name:
            return JsonResponse(
                {"success": False, "error": "Template name is required"}
            )

        # Convert order_id to int if provided
        if order_id:
            try:
                order_id = int(order_id)
            except (ValueError, TypeError):
                order_id = None

        # Generate preview
        preview_service = EmailTemplatePreviewService()
        preview = preview_service.generate_preview(
            template_name=template_name, order_id=order_id, language=language
        )

        # Build result
        if preview.error:
            result = {"success": False, "error": preview.error}
        else:
            result = {
                "success": True,
                "rendered_content": preview.html_content
                if format_type == "html"
                else preview.text_content,
                "format_type": format_type,
                "language": language,
                "data_source": "real" if order_id else "sample",
            }

        return JsonResponse(result)

    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON data"})
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)})


@staff_member_required
@require_http_methods(["GET"])
def get_template_info(request: HttpRequest, template_name: str) -> JsonResponse:
    """
    Get detailed information about a specific template.

    Args:
        request: HTTP request
        template_name: Name of the template

    Returns:
        JSON response with template information
    """
    try:
        from .registry import EmailTemplateRegistry

        registry = EmailTemplateRegistry()
        template_info = registry.get_template(template_name)

        if not template_info:
            return JsonResponse(
                {
                    "success": False,
                    "error": f"Template '{template_name}' not found",
                }
            )

        return JsonResponse(
            {
                "success": True,
                "template_info": {
                    "name": template_info.name,
                    "path": template_info.path,
                    "category": template_info.category,
                    "description": template_info.description,
                    "order_statuses": [
                        status.value for status in template_info.order_statuses
                    ],
                    "has_html": template_info.has_html,
                    "has_text": template_info.has_text,
                    "is_used": template_info.is_used,
                    "last_modified": template_info.last_modified.isoformat(),
                },
            }
        )

    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)})


@staff_member_required
@require_http_methods(["GET"])
def get_order_data(request: HttpRequest, order_id: int) -> JsonResponse:
    """
    Get order data for preview.

    Args:
        request: HTTP request
        order_id: Order ID

    Returns:
        JSON response with order data
    """
    try:
        order = (
            Order.objects.select_related()
            .prefetch_related("items__product")
            .get(id=order_id)
        )

        order_data = {
            "id": order.id,
            "status": order.status,
            "first_name": order.first_name,
            "last_name": order.last_name,
            "email": order.email,
            "paid_amount": str(order.paid_amount),
            "created_at": order.created_at.isoformat(),
            "items_count": order.items.count(),
        }

        return JsonResponse({"success": True, "order": order_data})

    except Order.DoesNotExist:
        return JsonResponse(
            {"success": False, "error": f"Order {order_id} not found"}
        )
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)})
