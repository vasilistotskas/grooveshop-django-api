"""Email template preview service for admin interface.

An order email previewed from a real order is the email that order would
be sent: ``order.emails`` builds the subject and the context for the
sending task and for this preview alike. Everything else, and every email
previewed on sample data, renders its template against made-up values and
shows no subject, because no real sender builds one for it yet (the
email-type catalogue will).
"""

import logging
from dataclasses import dataclass

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone

from core.email.config import EmailTemplateConfig
from core.email.sample_data import SampleOrderDataGenerator
from core.utils.email_context import build_email_context
from core.utils.tenant_urls import get_tenant_base_url
from order.emails import ORDER_EMAIL_KINDS, NothingToPreview
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
    order_id: int | None
    is_sample_data: bool
    error: str | None = None


class EmailTemplatePreviewService:
    """
    Service for generating email template previews.
    """

    def __init__(self):
        self.sample_generator = SampleOrderDataGenerator()

    def generate_preview(
        self,
        template_name: str,
        order_id: int | None = None,
        language: str = "el",
    ) -> EmailPreview:
        """Generate preview for a template."""
        from django.utils import translation

        # Store the current language to restore it later
        current_language = translation.get_language()

        try:
            # Activate the selected language for template rendering
            translation.activate(language)

            # Determine category from configuration
            category = self._extract_category(template_name)

            html_template, txt_template = self._template_paths(
                template_name, category
            )
            kind = ORDER_EMAIL_KINDS.get(template_name)

            if kind is not None and order_id:
                order = Order.objects.filter(pk=order_id).first()
                if order is None:
                    raise NothingToPreview(f"Order {order_id} not found.")
                email = kind.from_order(order, language)
                return EmailPreview(
                    template_name=template_name,
                    subject=email.subject,
                    html_content=self._render_template(
                        f"{email.template}.html", email.context
                    ),
                    text_content=self._render_template(
                        f"{email.template}.txt", email.context
                    ),
                    context_data=email.context,
                    order_id=order_id,
                    is_sample_data=False,
                )

            context = build_email_context(
                language=language,
                **self._get_sample_context(template_name),
            )
            return EmailPreview(
                template_name=template_name,
                subject=(
                    kind.sample_subject(context["order"]["id"])
                    if kind is not None
                    else ""
                ),
                html_content=self._render_template(html_template, context),
                text_content=self._render_template(txt_template, context),
                context_data=context,
                order_id=None,
                is_sample_data=True,
            )

        except NothingToPreview as e:
            # Not a failure: the chosen order simply has nothing this
            # email is about. Say so instead of rendering something else.
            return EmailPreview(
                template_name=template_name,
                subject="",
                html_content="",
                text_content="",
                context_data={},
                order_id=order_id,
                is_sample_data=False,
                error=str(e),
            )
        except Exception as e:
            logger.error(
                f"Error generating preview for template {template_name}: {e!s}",
                extra={"template_name": template_name, "order_id": order_id},
            )
            return EmailPreview(
                template_name=template_name,
                subject="",
                html_content="",
                text_content="",
                context_data={},
                order_id=order_id,
                is_sample_data=True,
                error=str(e),
            )
        finally:
            # Restore the original language
            translation.activate(current_language)

    def _template_paths(
        self, template_name: str, category: str | None
    ) -> tuple[str, str]:
        if category is None:
            return f"emails/{template_name}.html", f"emails/{template_name}.txt"
        return (
            f"emails/{category}/{template_name}.html",
            f"emails/{category}/{template_name}.txt",
        )

    def _get_sample_context(self, template_name: str) -> dict:
        """Made-up values for a template, chosen by its directory."""
        # Resolve the generator from the template's REAL directory, not
        # its name prefix: a name-based guess previewed invoice_issued and
        # admin_new_order with USER sample data and no order at all.
        generator_by_directory = {
            "order": "generate_order_context",
            "cart": "generate_order_context",
            "shipping_acs": "generate_order_context",
            "giftcard": "generate_order_context",
            "billing": "generate_user_context",
            "product": "generate_user_context",
            "subscription": "generate_subscription_context",
            "user": "generate_user_context",
        }
        directory = self._extract_category(template_name)
        generator_name = generator_by_directory.get(
            directory or "",
            EmailTemplateConfig.get_context_generator_for_template(
                template_name
            ),
        )
        if generator_name == "generate_subscription_context":
            return self._get_sample_subscription_context()
        if generator_name == "generate_user_context":
            return self._get_sample_user_context()
        return self._get_sample_order_context(template_name)

    def _get_sample_order_context(self, template_name: str = "") -> dict:
        """Generate sample order data for preview."""
        # Determine status based on template name
        status = self._get_status_from_template_name(template_name)

        sample_data = self.sample_generator.generate_order(status=status)

        # Add get_status_display method result
        sample_data["order"]["get_status_display"] = (
            self.sample_generator.get_status_display(
                sample_data["order"]["status"]
            )
        )

        return sample_data

    def _get_status_from_template_name(self, template_name: str) -> str:
        """Extract order status from template name."""
        status_map = {
            "order_shipped": "SHIPPED",
            "order_delivered": "DELIVERED",
            "order_completed": "COMPLETED",
            "order_canceled": "CANCELED",
            "order_returned": "RETURNED",
            "order_refunded": "REFUNDED",
            "order_status_generic": "PROCESSING",
        }
        return status_map.get(template_name, "PENDING")

    def _render_template(self, template_path: str, context: dict) -> str:
        """Render a template with a COMPLETE context (one from
        ``build_email_context`` or from an ``order.emails`` builder), and
        report a failure as the preview's text rather than raising."""
        from django.template import TemplateDoesNotExist, TemplateSyntaxError

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
            logger.exception(
                f"Template syntax error in {template_path}",
                extra={
                    "template_path": template_path,
                    "error": str(e),
                    "line_number": getattr(e, "lineno", None),
                },
            )
            return f"Template syntax error: {e!s}"
        except Exception as e:
            # Other rendering errors
            logger.exception(
                f"Error rendering template {template_path}",
                extra={
                    "template_path": template_path,
                    "context_keys": list(context.keys()),
                },
            )
            return f"Error rendering template: {e!s}"

    def _extract_category(self, template_name: str) -> str | None:
        """Resolve a template's directory from the REGISTRY, not its name.

        This used to call ``EmailTemplateConfig.get_category_for_template``,
        which only infers a directory for names starting with ``order_``
        or ``subscription_`` and returns None for everything else. The
        preview then built a ROOT-level path (``emails/<name>.html``)
        that does not exist, so seven live templates —
        acs_out_for_delivery, admin_new_order, boxnow_parcel_at_locker,
        data_export_ready, dispute_notification, invoice_issued and
        payment_failed — previewed as "Template not found".

        ``EmailTemplateInfo.path`` already carries the TRUE path the
        registry found on disk, so reading it back cannot disagree with
        reality and cannot regress when a directory is added.
        """
        from core.email.registry import EmailTemplateRegistry

        info = EmailTemplateRegistry().get_template(template_name)
        if info is not None:
            # path is "emails/<category>/<name>.html"; take <category>.
            parts = info.path.split("/")
            if len(parts) >= 3:
                return parts[1]
            return None
        return EmailTemplateConfig.get_category_for_template(template_name)

    def _get_available_template_list(self) -> list[str]:
        """Get list of available email templates for error messages."""
        import os

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
        from datetime import timedelta

        return {
            "user": {
                "id": 12345,
                "username": "john_doe",
                "first_name": "John",
                "last_name": "Doe",
                "email": "john.doe@example.com",
                "date_joined": timezone.now() - timedelta(days=180),
                "last_login": timezone.now() - timedelta(days=45),
            },
            "app_base_url": get_tenant_base_url(),
            "week_start": timezone.now() - timedelta(days=7),
            "week_end": timezone.now(),
            "featured_articles": [
                {
                    "title": "New Product Launch",
                    "summary": "Check out our latest products",
                    "url": f"{get_tenant_base_url()}/blog/new-product-launch",
                },
                {
                    "title": "Customer Success Story",
                    "summary": "How our customers achieve their goals",
                    "url": f"{get_tenant_base_url()}/blog/success-story",
                },
            ],
            "unsubscribe_url": f"{get_tenant_base_url()}/unsubscribe",
            "preferences_url": f"{get_tenant_base_url()}/preferences",
        }

    def _get_sample_subscription_context(self) -> dict:
        """Generate sample subscription data for subscription templates."""

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
                "start_date": timezone.now(),
                "billing_cycle": "monthly",
                "amount": "€9.99",
            },
            "app_base_url": get_tenant_base_url(),
        }
