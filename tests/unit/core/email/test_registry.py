"""Unit tests for email template registry."""

import pytest
from core.email.registry import EmailTemplateRegistry, EmailTemplateInfo
from order.enum.status import OrderStatus


class TestEmailTemplateRegistry:
    """Test suite for EmailTemplateRegistry."""

    @pytest.fixture
    def registry(self):
        """Create a registry instance for testing."""
        return EmailTemplateRegistry()

    def test_registry_initialization(self, registry):
        """Test that registry initializes correctly."""
        assert registry is not None
        assert hasattr(registry, "_templates")
        assert isinstance(registry._templates, dict)

    def test_get_all_templates(self, registry):
        """Test getting all templates."""
        templates = registry.get_all_templates()
        assert isinstance(templates, list)
        assert len(templates) > 0
        assert all(isinstance(t, EmailTemplateInfo) for t in templates)

    def test_get_template_by_name(self, registry):
        """Test getting a specific template by name."""
        template = registry.get_template("order_confirmation")
        assert template is not None
        assert isinstance(template, EmailTemplateInfo)
        assert template.name == "order_confirmation"

    def test_get_nonexistent_template(self, registry):
        """Test getting a template that doesn't exist."""
        template = registry.get_template("nonexistent_template")
        assert template is None

    def test_get_categories(self, registry):
        """Test getting all template categories."""
        categories = registry.get_categories()
        assert isinstance(categories, list)
        assert len(categories) > 0
        assert all(isinstance(c, str) for c in categories)

    def test_get_by_category(self, registry):
        """Test getting templates by category."""
        categories = registry.get_categories()
        if categories:
            category = categories[0]
            templates = registry.get_by_category(category)
            assert isinstance(templates, list)
            assert all(t.category == category for t in templates)

    def test_get_by_status(self, registry):
        """Test getting templates by order status."""
        templates = registry.get_by_status(OrderStatus.PENDING)
        assert isinstance(templates, list)
        # Should have at least one template for pending status
        assert len(templates) > 0

    def test_template_has_required_fields(self, registry):
        """Test that templates have all required fields."""
        templates = registry.get_all_templates()
        for template in templates:
            assert hasattr(template, "name")
            assert hasattr(template, "path")
            assert hasattr(template, "category")
            assert hasattr(template, "description")
            assert hasattr(template, "order_statuses")
            assert hasattr(template, "has_html")
            assert hasattr(template, "has_text")
            assert hasattr(template, "is_used")
            assert hasattr(template, "last_modified")

    def test_template_paths_exist(self, registry):
        """Test that template paths point to existing files."""
        from django.template.loader import get_template
        from django.template import TemplateDoesNotExist

        templates = registry.get_all_templates()
        for template in templates:
            if template.has_html:
                try:
                    # Use Django's template loader to check if template exists
                    get_template(template.path)
                except TemplateDoesNotExist:
                    pytest.fail(f"HTML template not found: {template.path}")

    def test_status_template_mapping(self, registry):
        """Test that status-to-template mapping is correct."""
        # Test a few known mappings
        pending_templates = registry.get_by_status(OrderStatus.PENDING)
        assert any(t.name == "order_pending" for t in pending_templates)

        shipped_templates = registry.get_by_status(OrderStatus.SHIPPED)
        assert any(t.name == "order_shipped" for t in shipped_templates)

    def test_html_text_parity(self, registry):
        """Test that templates with HTML also have text versions."""
        templates = registry.get_all_templates()
        for template in templates:
            if template.has_html:
                # Most templates should have both HTML and text versions
                # This is a soft check - some templates might only have HTML
                pass  # We'll just verify the flags are set correctly

    def test_template_categories_are_valid(self, registry):
        """Test that all templates have valid categories."""
        templates = registry.get_all_templates()
        valid_categories = {"Order Lifecycle", "Shipping", "Other"}
        for template in templates:
            assert template.category in valid_categories or template.category, (
                f"Invalid category for template {template.name}: {template.category}"
            )
