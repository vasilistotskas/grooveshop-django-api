"""Optional preview metadata for the admin email-template page.

Read by ``registry.py`` and ``preview_service.py`` and by nothing else — no
sending path imports this module. Templates are discovered from disk, so an
entry here is never required for a template to exist, be listed, or be sent;
it only replaces the fallback description and category with better ones.
A subject is never kept here: the preview shows the one the sender builds
(``order.emails``).
"""

from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _
from django_stubs_ext import StrOrPromise

from order.enum.status import OrderStatus


@dataclass
class TemplateCategory:
    """Configuration for a template category."""

    name: StrOrPromise  # Category name for display
    path: str  # Subdirectory path (empty string for root level)
    context_generator: str  # Method name to generate context


@dataclass
class TemplateConfig:
    """Configuration for a single template."""

    name: str  # Template file name (without extension)
    category_name: StrOrPromise  # Display category
    description: StrOrPromise  # Template description
    is_used: bool  # Whether template is actively used
    order_statuses: list[OrderStatus] | None = (
        None  # Associated order statuses (optional, only for order templates)
    )

    def __post_init__(self):
        """Initialize optional fields with defaults."""
        if self.order_statuses is None:
            self.order_statuses = []


class EmailTemplateConfig:
    """Metadata for the subset of templates worth describing by hand.

    Describes only some of the templates on disk; the rest fall back to
    their directory and a placeholder description in ``registry.py``.
    """

    # Category configurations
    # The admin heading of every template directory under
    # ``core/templates/emails``. A directory missing here is a bug, caught by
    # ``test_every_template_directory_has_a_heading``.
    DIRECTORY_HEADINGS: dict[str, StrOrPromise] = {
        "order": _("Order Lifecycle"),
        "subscription": _("Subscription"),
        "user": _("User Management"),
        "staff": _("Staff"),
        "b2b": _("B2B"),
        "billing": _("Billing"),
        "cart": _("Cart"),
        "contact": _("Contact"),
        "giftcard": _("Gift Cards"),
        "product": _("Product"),
        "shipping_acs": _("ACS Shipping"),
    }

    CATEGORIES: dict[str, TemplateCategory] = {
        "order": TemplateCategory(
            name=DIRECTORY_HEADINGS["order"],
            path="order",
            context_generator="generate_order_context",
        ),
        "subscription": TemplateCategory(
            name=DIRECTORY_HEADINGS["subscription"],
            path="subscription",
            context_generator="generate_subscription_context",
        ),
        "user": TemplateCategory(
            name=DIRECTORY_HEADINGS["user"],
            path="user",
            context_generator="generate_user_context",
        ),
    }

    # Template configurations
    TEMPLATES: dict[str, TemplateConfig] = {
        # Order templates
        "order_shipped": TemplateConfig(
            name="order_shipped",
            category_name=_("Order Lifecycle"),
            description=_("Sent when order is shipped"),
            order_statuses=[OrderStatus.SHIPPED],
            is_used=True,
        ),
        "order_delivered": TemplateConfig(
            name="order_delivered",
            category_name=_("Order Lifecycle"),
            description=_("Sent when order is delivered"),
            order_statuses=[OrderStatus.DELIVERED],
            is_used=True,
        ),
        "order_canceled": TemplateConfig(
            name="order_canceled",
            category_name=_("Order Lifecycle"),
            description=_("Sent when order is canceled"),
            order_statuses=[OrderStatus.CANCELED],
            is_used=True,
        ),
        "order_status_generic": TemplateConfig(
            name="order_status_generic",
            category_name=_("Order Lifecycle"),
            description=_("Generic template for any status update"),
            order_statuses=[],
            is_used=True,
        ),
        "order_completed": TemplateConfig(
            name="order_completed",
            category_name=_("Order Lifecycle"),
            description=_("Status update for completed orders"),
            order_statuses=[OrderStatus.COMPLETED],
            is_used=True,
        ),
        "order_refunded": TemplateConfig(
            name="order_refunded",
            category_name=_("Order Lifecycle"),
            description=_("Status update for refunded orders"),
            order_statuses=[OrderStatus.REFUNDED],
            is_used=True,
        ),
        "order_returned": TemplateConfig(
            name="order_returned",
            category_name=_("Order Lifecycle"),
            description=_("Status update for returned orders"),
            order_statuses=[OrderStatus.RETURNED],
            is_used=True,
        ),
        # Subscription templates
        "confirmation": TemplateConfig(
            name="confirmation",
            category_name=_("Subscription"),
            description=_("Subscription confirmation email"),
            is_used=True,
        ),
        # User management templates
        "inactive_user_email_template": TemplateConfig(
            name="inactive_user_email_template",
            category_name=_("User Management"),
            description=_("Inactive user notification"),
            is_used=True,
        ),
    }

    @classmethod
    def get_category_for_template(cls, template_name: str) -> str | None:
        """
        Get category path for a template name.

        Args:
            template_name: Template name

        Returns:
            Category path or None if not found
        """
        # Check if template is in configuration
        if template_name in cls.TEMPLATES:
            config = cls.TEMPLATES[template_name]
            # Find category by name
            for category in cls.CATEGORIES.values():
                if category.name == config.category_name:
                    return category.path if category.path else None

        # Fallback: infer from prefix
        if template_name.startswith("order_"):
            return "order"
        elif template_name.startswith("subscription_"):
            return "subscription"

        return None

    @classmethod
    def get_context_generator_for_template(
        cls, template_name: str
    ) -> str | None:
        """
        Get context generator method name for a template.

        Args:
            template_name: Template name

        Returns:
            Context generator method name or None
        """
        if template_name in cls.TEMPLATES:
            config = cls.TEMPLATES[template_name]
            for category in cls.CATEGORIES.values():
                if category.name == config.category_name:
                    return category.context_generator

        # Fallback
        if template_name.startswith("order_"):
            return "generate_order_context"
        return "generate_user_context"

    @classmethod
    def get_template_config(cls, template_name: str) -> TemplateConfig | None:
        """
        Get configuration for a template.

        Args:
            template_name: Template name

        Returns:
            Template configuration or None
        """
        return cls.TEMPLATES.get(template_name)
