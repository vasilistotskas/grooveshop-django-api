"""Configuration for email template management system."""

from dataclasses import dataclass
from typing import Optional


from order.enum.status import OrderStatus


@dataclass
class TemplateCategory:
    """Configuration for a template category."""

    name: str  # Category name for display
    path: str  # Subdirectory path (empty string for root level)
    context_generator: str  # Method name to generate context
    templates: dict[str, "TemplateConfig"]  # Template configurations


@dataclass
class TemplateConfig:
    """Configuration for a single template."""

    name: str  # Template file name (without extension)
    category_name: str  # Display category
    description: str  # Template description
    subject_template: str  # Subject line template with {variables}
    is_used: bool  # Whether template is actively used
    context_keys: list[str]  # Required context keys for validation
    order_statuses: list[OrderStatus] = (
        None  # Associated order statuses (optional, only for order templates)
    )

    def __post_init__(self):
        """Initialize optional fields with defaults."""
        if self.order_statuses is None:
            self.order_statuses = []


class EmailTemplateConfig:
    """
    Centralized configuration for all email templates.
    This makes the system fully dynamic and easy to extend.
    """

    # Category configurations
    CATEGORIES: dict[str, TemplateCategory] = {
        "order": TemplateCategory(
            name="Order Lifecycle",
            path="order",
            context_generator="generate_order_context",
            templates={},  # Populated below
        ),
        "subscription": TemplateCategory(
            name="Subscription",
            path="subscription",
            context_generator="generate_subscription_context",
            templates={},
        ),
        "user": TemplateCategory(
            name="User Management",
            path="user",
            context_generator="generate_user_context",
            templates={},
        ),
        "marketing": TemplateCategory(
            name="Marketing",
            path="marketing",
            context_generator="generate_marketing_context",
            templates={},
        ),
    }

    # Template configurations
    TEMPLATES: dict[str, TemplateConfig] = {
        # Order templates
        "order_confirmation": TemplateConfig(
            name="order_confirmation",
            category_name="Order Lifecycle",
            description="Sent when a new order is created",
            subject_template="Order Confirmation - #{order[id]}",
            order_statuses=[OrderStatus.PENDING],
            is_used=True,
            context_keys=["order", "items"],
        ),
        "order_shipped": TemplateConfig(
            name="order_shipped",
            category_name="Order Lifecycle",
            description="Sent when order is shipped",
            subject_template="Your Order #{order[id]} Has Shipped",
            order_statuses=[OrderStatus.SHIPPED],
            is_used=True,
            context_keys=["order", "items", "tracking_number", "carrier"],
        ),
        "order_delivered": TemplateConfig(
            name="order_delivered",
            category_name="Order Lifecycle",
            description="Sent when order is delivered",
            subject_template="Your Order #{order[id]} Has Been Delivered",
            order_statuses=[OrderStatus.DELIVERED],
            is_used=True,
            context_keys=["order", "items"],
        ),
        "order_canceled": TemplateConfig(
            name="order_canceled",
            category_name="Order Lifecycle",
            description="Sent when order is canceled",
            subject_template="Your Order #{order[id]} Has Been Canceled",
            order_statuses=[OrderStatus.CANCELED],
            is_used=True,
            context_keys=["order", "items"],
        ),
        "order_pending_reminder": TemplateConfig(
            name="order_pending_reminder",
            category_name="Order Lifecycle",
            description="Reminder for pending orders after 24 hours",
            subject_template="Reminder: Complete Your Order #{order[id]}",
            order_statuses=[OrderStatus.PENDING],
            is_used=True,
            context_keys=["order", "items"],
        ),
        "order_status_generic": TemplateConfig(
            name="order_status_generic",
            category_name="Order Lifecycle",
            description="Generic template for any status update",
            subject_template="Order #{order[id]} Status Update",
            order_statuses=[],
            is_used=True,
            context_keys=["order", "items"],
        ),
        "order_pending": TemplateConfig(
            name="order_pending",
            category_name="Order Lifecycle",
            description="Status update for pending orders",
            subject_template="Order #{order[id]} - Pending",
            order_statuses=[OrderStatus.PENDING],
            is_used=False,
            context_keys=["order", "items"],
        ),
        "order_processing": TemplateConfig(
            name="order_processing",
            category_name="Order Lifecycle",
            description="Status update for processing orders",
            subject_template="Order #{order[id]} - Processing",
            order_statuses=[OrderStatus.PROCESSING],
            is_used=False,
            context_keys=["order", "items"],
        ),
        "order_completed": TemplateConfig(
            name="order_completed",
            category_name="Order Lifecycle",
            description="Status update for completed orders",
            subject_template="Order #{order[id]} - Completed",
            order_statuses=[OrderStatus.COMPLETED],
            is_used=False,
            context_keys=["order", "items"],
        ),
        "order_refunded": TemplateConfig(
            name="order_refunded",
            category_name="Order Lifecycle",
            description="Status update for refunded orders",
            subject_template="Order #{order[id]} - Refunded",
            order_statuses=[OrderStatus.REFUNDED],
            is_used=False,
            context_keys=["order", "items"],
        ),
        "order_returned": TemplateConfig(
            name="order_returned",
            category_name="Order Lifecycle",
            description="Status update for returned orders",
            subject_template="Order #{order[id]} - Returned",
            order_statuses=[OrderStatus.RETURNED],
            is_used=False,
            context_keys=["order", "items"],
        ),
        # Subscription templates
        "confirmation": TemplateConfig(
            name="confirmation",
            category_name="Subscription",
            description="Subscription confirmation email",
            subject_template="Subscription Confirmed",
            is_used=True,
            context_keys=["user", "subscription"],
        ),
        # User management templates
        "inactive_user_email_template": TemplateConfig(
            name="inactive_user_email_template",
            category_name="User Management",
            description="Inactive user notification",
            subject_template="We Miss You, {user[first_name]}!",
            is_used=True,
            context_keys=["user", "app_base_url"],
        ),
        "password_reset": TemplateConfig(
            name="password_reset",
            category_name="User Management",
            description="Password reset request",
            subject_template="Reset Your Password",
            is_used=False,
            context_keys=["user", "reset_link"],
        ),
        "welcome": TemplateConfig(
            name="welcome",
            category_name="User Management",
            description="Welcome email for new users",
            subject_template="Welcome to {SITE_NAME}, {user[first_name]}!",
            is_used=False,
            context_keys=["user", "activation_link"],
        ),
        # Marketing templates
        "newsletter": TemplateConfig(
            name="newsletter",
            category_name="Marketing",
            description="Newsletter template",
            subject_template="Weekly Newsletter - {user[first_name]}",
            is_used=True,
            context_keys=[
                "user",
                "week_start",
                "week_end",
                "featured_articles",
            ],
        ),
        "promotion": TemplateConfig(
            name="promotion",
            category_name="Marketing",
            description="Promotional email",
            subject_template="Special Offer for {user[first_name]}!",
            is_used=False,
            context_keys=["user", "promotion"],
        ),
    }

    @classmethod
    def get_category_for_template(cls, template_name: str) -> Optional[str]:
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
            for category_key, category in cls.CATEGORIES.items():
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
    ) -> Optional[str]:
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
    def get_template_config(
        cls, template_name: str
    ) -> Optional[TemplateConfig]:
        """
        Get configuration for a template.

        Args:
            template_name: Template name

        Returns:
            Template configuration or None
        """
        return cls.TEMPLATES.get(template_name)

    @classmethod
    def get_all_categories(cls) -> list[str]:
        """Get all category names."""
        return list(set(t.category_name for t in cls.TEMPLATES.values()))

    @classmethod
    def get_templates_by_category(
        cls, category_name: str
    ) -> list[TemplateConfig]:
        """Get all templates in a category."""
        return [
            config
            for config in cls.TEMPLATES.values()
            if config.category_name == category_name
        ]
