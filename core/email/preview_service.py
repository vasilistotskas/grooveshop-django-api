"""Email template preview service for admin interface."""

import logging
from dataclasses import dataclass
from typing import Optional

from django.conf import settings
from django.template.loader import render_to_string

from core.email.config import EmailTemplateConfig
from core.email.sample_data import SampleOrderDataGenerator
from order.models import Order

logger = logging.getLogger(__name__)


@dataclass
class EmailPreview:
    """Preview data for an email template."""

    template_name: str
    subject: str
    html_content: str
    text_content: str
    context_data: dict
    order_id: Optional[int]
    is_sample_data: bool
    error: Optional[str] = None


class EmailTemplatePreviewService:
    """
    Service for generating email template previews.
    """

    def __init__(self):
        self.sample_generator = SampleOrderDataGenerator()

    def generate_preview(
        self, template_name: str, order_id: Optional[int] = None
    ) -> EmailPreview:
        """Generate preview for a template."""
        try:
            # Determine category from configuration
            category = self._extract_category(template_name)

            # Get context data based on template configuration
            context, is_sample = self._get_context_data_for_category(
                template_name, order_id
            )

            # Determine template paths based on category
            if category is None:
                # Root level templates
                html_template = f"emails/{template_name}.html"
                txt_template = f"emails/{template_name}.txt"
            else:
                html_template = f"emails/{category}/{template_name}.html"
                txt_template = f"emails/{category}/{template_name}.txt"

            # Render templates
            html_content = self._render_template(html_template, context)
            text_content = self._render_template(txt_template, context)

            # Generate subject
            subject = self._generate_subject(template_name, context)

            return EmailPreview(
                template_name=template_name,
                subject=subject,
                html_content=html_content,
                text_content=text_content,
                context_data=context,
                order_id=order_id if not is_sample else None,
                is_sample_data=is_sample,
            )

        except Exception as e:
            logger.error(
                f"Error generating preview for template {template_name}: {e!s}",
                extra={"template_name": template_name, "order_id": order_id},
            )
            return EmailPreview(
                template_name=template_name,
                subject="Error",
                html_content="",
                text_content="",
                context_data={},
                order_id=order_id,
                is_sample_data=True,
                error=str(e),
            )

    def _get_context_data_for_category(
        self, template_name: str, order_id: Optional[int] = None
    ) -> tuple[dict, bool]:
        """Get context data for template rendering based on configuration."""
        generator_name = EmailTemplateConfig.get_context_generator_for_template(
            template_name
        )

        # Map generator names to methods
        generator_map = {
            "generate_order_context": lambda: self._get_context_data(order_id),
            "generate_subscription_context": lambda: (
                self._get_sample_subscription_context(),
                True,
            ),
            "generate_user_context": lambda: (
                self._get_sample_user_context(),
                True,
            ),
            "generate_marketing_context": lambda: (
                self._get_sample_user_context(),
                True,
            ),
        }

        generator = generator_map.get(generator_name)
        if generator:
            result = generator()
            # Handle both tuple and single dict returns
            if isinstance(result, tuple):
                return result
            return result, True

        # Fallback to order context
        return self._get_context_data(order_id)

    def _get_context_data(self, order_id: Optional[int]) -> tuple[dict, bool]:
        """Get context data for order templates."""
        if order_id:
            try:
                return self._get_real_order_context(order_id), False
            except Order.DoesNotExist:
                logger.warning(
                    f"Order {order_id} not found, falling back to sample data",
                    extra={"order_id": order_id},
                )
        return self._get_sample_order_context(), True

    def _get_real_order_context(self, order_id: int) -> dict:
        """Load real order data for preview."""
        order = Order.objects.get(id=order_id)
        items = order.items.all()

        # Format items for template
        formatted_items = []
        for item in items:
            # Handle Money objects by accessing .amount attribute
            item_price = (
                float(item.price.amount)
                if hasattr(item.price, "amount")
                else float(item.price)
            )
            item_total = (
                float(item.total_price.amount)
                if hasattr(item.total_price, "amount")
                else float(item.total_price)
            )

            formatted_items.append(
                {
                    "id": item.id,
                    "product": {
                        "id": item.product.id,
                        "name": item.product.name,
                    },
                    "quantity": item.quantity,
                    "price": f"€{item_price:.2f}",
                    "total_price": f"€{item_total:.2f}",
                    "get_total_price": f"€{item_total:.2f}",
                }
            )

        return {
            "order": {
                "id": order.id,
                "uuid": str(order.uuid),
                "status": order.status,
                "get_status_display": order.get_status_display(),
                "first_name": order.first_name,
                "last_name": order.last_name,
                "email": order.email,
                "phone": order.phone or "",
                "street": order.street,
                "street_number": order.street_number,
                "city": order.city,
                "zipcode": order.zipcode,
                "country": str(order.country) if order.country else "Greece",
                "total_price": f"€{float(order.total_price.amount) if hasattr(order.total_price, 'amount') else float(order.total_price):.2f}",
                "total_price_items": f"€{float(order.total_price_items.amount) if hasattr(order.total_price_items, 'amount') else float(order.total_price_items):.2f}",
                "shipping_price": f"€{float(order.shipping_price.amount) if hasattr(order.shipping_price, 'amount') else float(order.shipping_price):.2f}",
                "paid_amount": f"€{float(order.total_price.amount) if hasattr(order.total_price, 'amount') else float(order.total_price):.2f}",
                "created_at": order.created_at,
                "status_updated_at": order.status_updated_at
                or order.created_at,
                "tracking_number": order.tracking_number or "",
                "shipping_carrier": order.shipping_carrier or "",
            },
            "items": formatted_items,
            "tracking_number": order.tracking_number or "",
            "carrier": order.shipping_carrier or "",
        }

    def _get_sample_order_context(self) -> dict:
        """Generate sample order data for preview."""
        sample_data = self.sample_generator.generate_order()

        # Add get_status_display method result
        sample_data["order"]["get_status_display"] = (
            self.sample_generator.get_status_display(
                sample_data["order"]["status"]
            )
        )

        return sample_data

    def _render_template(self, template_path: str, context: dict) -> str:
        """Render template with context."""
        from django.template import TemplateDoesNotExist, TemplateSyntaxError
        import os

        # Add context processor variables that are needed for email templates
        # These match what's provided by core.context_processors.metadata
        context = {
            **context,
            "STATIC_BASE_URL": settings.STATIC_BASE_URL,
            "SITE_NAME": os.getenv("SITE_NAME", "Grooveshop"),
            "SITE_URL": settings.NUXT_BASE_URL,
            "INFO_EMAIL": settings.INFO_EMAIL,
            "LANGUAGE_CODE": settings.LANGUAGE_CODE,
        }

        try:
            return render_to_string(template_path, context)
        except TemplateDoesNotExist as e:
            # Template file not found
            logger.warning(
                f"Template not found: {template_path}",
                extra={
                    "template_path": template_path,
                    "error": str(e),
                    "available_templates": self._get_available_template_list(),
                },
            )
            return f"Template not found: {template_path}. Please check that the template file exists."
        except TemplateSyntaxError as e:
            # Template has syntax errors
            logger.error(
                f"Template syntax error in {template_path}: {e!s}",
                extra={
                    "template_path": template_path,
                    "error": str(e),
                    "line_number": getattr(e, "lineno", None),
                },
                exc_info=True,
            )
            return f"Template syntax error: {e!s}"
        except Exception as e:
            # Other rendering errors
            logger.error(
                f"Error rendering template {template_path}: {e!s}",
                extra={
                    "template_path": template_path,
                    "context_keys": list(context.keys()),
                },
                exc_info=True,
            )
            return f"Error rendering template: {e!s}"

    def _extract_category(self, template_name: str) -> Optional[str]:
        """Extract category from template name using configuration."""
        category = EmailTemplateConfig.get_category_for_template(template_name)
        # Return None for root level (empty string means root)
        return category if category else None

    def _get_available_template_list(self) -> list[str]:
        """Get list of available email templates for error messages."""
        import os
        from django.conf import settings

        templates = []
        emails_dir = os.path.join(
            settings.BASE_DIR, "core", "templates", "emails"
        )

        try:
            # Scan all subdirectories
            for root, dirs, files in os.walk(emails_dir):
                for f in files:
                    if f.endswith((".html", ".txt")):
                        templates.append(f)
        except Exception as e:
            logger.error(f"Error listing templates: {e!s}")
        return templates

    def _get_sample_user_context(self) -> dict:
        """Generate sample user data for user-related templates."""
        from datetime import datetime, timedelta

        return {
            "user": {
                "id": 12345,
                "username": "john_doe",
                "first_name": "John",
                "last_name": "Doe",
                "email": "john.doe@example.com",
                "date_joined": datetime.now() - timedelta(days=180),
                "last_login": datetime.now() - timedelta(days=45),
            },
            "app_base_url": settings.NUXT_BASE_URL,
            "week_start": datetime.now() - timedelta(days=7),
            "week_end": datetime.now(),
            "featured_articles": [
                {
                    "title": "New Product Launch",
                    "summary": "Check out our latest products",
                    "url": f"{settings.NUXT_BASE_URL}/blog/new-product-launch",
                },
                {
                    "title": "Customer Success Story",
                    "summary": "How our customers achieve their goals",
                    "url": f"{settings.NUXT_BASE_URL}/blog/success-story",
                },
            ],
            "unsubscribe_url": f"{settings.NUXT_BASE_URL}/unsubscribe",
            "preferences_url": f"{settings.NUXT_BASE_URL}/preferences",
        }

    def _get_sample_subscription_context(self) -> dict:
        """Generate sample subscription data for subscription templates."""
        from datetime import datetime

        return {
            "user": {
                "id": 12345,
                "username": "john_doe",
                "first_name": "John",
                "last_name": "Doe",
                "email": "john.doe@example.com",
            },
            "subscription": {
                "id": 67890,
                "plan": "Premium",
                "status": "active",
                "start_date": datetime.now(),
                "billing_cycle": "monthly",
                "amount": "€9.99",
            },
            "app_base_url": settings.NUXT_BASE_URL,
        }

    def _generate_subject(self, template_name: str, context: dict) -> str:
        """Generate email subject based on template configuration."""
        config = EmailTemplateConfig.get_template_config(template_name)

        if config and config.subject_template:
            try:
                # Use string formatting with context
                # Support both {key} and {dict[key]} syntax
                subject = config.subject_template
                for key, value in context.items():
                    if isinstance(value, dict):
                        # Handle nested dict access like {order[id]}
                        for subkey, subvalue in value.items():
                            placeholder = f"{{{key}[{subkey}]}}"
                            if placeholder in subject:
                                subject = subject.replace(
                                    placeholder, str(subvalue)
                                )
                    else:
                        # Handle simple {key} access
                        placeholder = f"{{{key}}}"
                        if placeholder in subject:
                            subject = subject.replace(placeholder, str(value))
                return subject
            except Exception as e:
                logger.warning(
                    f"Error formatting subject for {template_name}: {e}",
                    extra={"template_name": template_name, "error": str(e)},
                )

        # Fallback to generic subject
        return f"Email from {settings.SITE_NAME if hasattr(settings, 'SITE_NAME') else 'Grooveshop'}"
