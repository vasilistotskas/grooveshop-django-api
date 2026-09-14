"""Optional preview metadata for the admin email-template page.

Read by ``registry.py`` and ``preview_service.py`` and by nothing else — no
sending path imports this module. Templates are discovered from disk, so an
entry here is never required for a template to exist, be listed, or be sent;
it only replaces the fallback description and category with better ones and
supplies the subject the PREVIEW renders. The subject a customer actually
receives is built by the task that sends the mail.
"""

from dataclasses import dataclass

from order.enum.status import OrderStatus


@dataclass
class TemplateCategory:
    """Configuration for a template category."""

    name: str  # Category name for display
    path: str  # Subdirectory path (empty string for root level)
    context_generator: str  # Method name to generate context


@dataclass
class TemplateConfig:
    """Configuration for a single template."""

    name: str  # Template file name (without extension)
    category_name: str  # Display category
    description: str  # Template description
    subject_template: str  # Subject line template with {variables}
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

    Covers 10 of the templates on disk; the rest fall back to their
    directory and a placeholder description in ``registry.py``.
    """

    # Category configurations
    CATEGORIES: dict[str, TemplateCategory] = {
        "order": TemplateCategory(
            name="Order Lifecycle",
            path="order",
            context_generator="generate_order_context",
        ),
        "subscription": TemplateCategory(
            name="Subscription",
            path="subscription",
            context_generator="generate_subscription_context",
        ),
        "user": TemplateCategory(
            name="User Management",
            path="user",
            context_generator="generate_user_context",
        ),
    }

    # Template configurations
    TEMPLATES: dict[str, TemplateConfig] = {
        # Order templates
        "order_shipped": TemplateConfig(
            name="order_shipped",
            category_name="Order Lifecycle",
            description="Sent when order is shipped",
            subject_template="Your Order #{order[id]} Has Shipped",
            order_statuses=[OrderStatus.SHIPPED],
            is_used=True,
        ),
        "order_delivered": TemplateConfig(
            name="order_delivered",
            category_name="Order Lifecycle",
            description="Sent when order is delivered",
            subject_template="Your Order #{order[id]} Has Been Delivered",
            order_statuses=[OrderStatus.DELIVERED],
            is_used=True,
        ),
        "order_canceled": TemplateConfig(
            name="order_canceled",
            category_name="Order Lifecycle",
            description="Sent when order is canceled",
            subject_template="Your Order #{order[id]} Has Been Canceled",
            order_statuses=[OrderStatus.CANCELED],
            is_used=True,
        ),
        "order_pending_reminder": TemplateConfig(
            name="order_pending_reminder",
            category_name="Order Lifecycle",
            description="Reminder for pending orders after 24 hours",
            subject_template="Reminder: Complete Your Order #{order[id]}",
            order_statuses=[OrderStatus.PENDING],
            is_used=True,
        ),
        "order_status_generic": TemplateConfig(
            name="order_status_generic",
            category_name="Order Lifecycle",
            description="Generic template for any status update",
            subject_template="Order #{order[id]} Status Update",
            order_statuses=[],
            is_used=True,
        ),
        "order_completed": TemplateConfig(
            name="order_completed",
            category_name="Order Lifecycle",
            description="Status update for completed orders",
            subject_template="Order #{order[id]} - Completed",
            order_statuses=[OrderStatus.COMPLETED],
            is_used=True,
        ),
        "order_refunded": TemplateConfig(
            name="order_refunded",
            category_name="Order Lifecycle",
            description="Status update for refunded orders",
            subject_template="Order #{order[id]} - Refunded",
            order_statuses=[OrderStatus.REFUNDED],
            is_used=True,
        ),
        "order_returned": TemplateConfig(
            name="order_returned",
            category_name="Order Lifecycle",
            description="Status update for returned orders",
            subject_template="Order #{order[id]} - Returned",
            order_statuses=[OrderStatus.RETURNED],
            is_used=True,
        ),
        # Subscription templates
        "confirmation": TemplateConfig(
            name="confirmation",
            category_name="Subscription",
            description="Subscription confirmation email",
            subject_template="Subscription Confirmed",
            is_used=True,
        ),
        # User management templates
        "inactive_user_email_template": TemplateConfig(
            name="inactive_user_email_template",
            category_name="User Management",
            description="Inactive user notification",
            subject_template="We Miss You, {user[first_name]}!",
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
