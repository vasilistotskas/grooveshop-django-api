"""Unit tests for email template preview service."""

from unittest.mock import patch

import pytest

from core.email.preview_service import EmailPreview, EmailTemplatePreviewService
from order.factories.order import OrderFactory


class TestEmailTemplatePreviewService:
    """Test suite for EmailTemplatePreviewService."""

    @pytest.fixture
    def service(self):
        """Create a service instance for testing."""
        return EmailTemplatePreviewService()

    def test_service_initialization(self, service):
        """Test that service initializes correctly."""
        assert service is not None
        assert hasattr(service, "sample_generator")

    def test_generate_preview_with_sample_data(self, service):
        """Test generating preview with sample data."""
        preview = service.generate_preview(
            template_name="order_completed", order_id=None
        )

        assert isinstance(preview, EmailPreview)
        assert preview.template_name == "order_completed"
        assert preview.html_content is not None
        assert preview.text_content is not None
        assert preview.error is None

    @pytest.mark.django_db
    def test_generate_preview_with_real_order(self, service):
        """A real order renders the email that order would be sent."""
        order = OrderFactory(num_order_items=1)

        preview = service.generate_preview(
            template_name="order_completed", order_id=order.id
        )

        assert isinstance(preview, EmailPreview)
        assert preview.error is None
        assert preview.is_sample_data is False
        assert preview.order_id == order.id
        assert f"#{order.id}" in preview.subject

    def test_generate_preview_with_invalid_template(self, service):
        """Test generating preview with invalid template name."""
        preview = service.generate_preview(
            template_name="nonexistent_template", order_id=None
        )

        assert isinstance(preview, EmailPreview)
        # Template not found should result in error message in content, not error field
        assert (
            "not found" in preview.html_content.lower()
            or "not found" in preview.text_content.lower()
        )

    @pytest.mark.django_db
    def test_generate_preview_with_invalid_order_id(self, service):
        """An order that does not exist is reported, not replaced by
        sample data that would pass for the order's email."""
        preview = service.generate_preview(
            template_name="order_completed", order_id=99999
        )

        assert preview.error is not None
        assert preview.html_content == ""

    def test_preview_contains_html_content(self, service):
        """Test that preview contains HTML content."""
        preview = service.generate_preview(
            template_name="order_completed", order_id=None
        )

        if preview.error is None:
            assert preview.html_content is not None
            assert len(preview.html_content) > 0
            assert (
                "<html" in preview.html_content.lower()
                or "<!doctype" in preview.html_content.lower()
            )

    def test_preview_contains_text_content(self, service):
        """Test that preview contains text content."""
        preview = service.generate_preview(
            template_name="order_completed", order_id=None
        )

        if preview.error is None:
            assert preview.text_content is not None
            assert len(preview.text_content) > 0

    def test_preview_context_data_is_dict(self, service):
        """Test that preview context data is a dictionary."""
        preview = service.generate_preview(
            template_name="order_completed", order_id=None
        )

        if preview.error is None:
            assert isinstance(preview.context_data, dict)

    def test_get_sample_order_context(self, service):
        """Test getting sample order context."""
        context = service._get_sample_order_context()

        assert isinstance(context, dict)
        assert "order" in context
        # Context may not have site_name/site_url at this level
        # Those are added by context processors during rendering

    def test_render_template_with_valid_data(self, service):
        """Test rendering template with valid data."""
        context = service._get_sample_order_context()

        # _render_template returns a single string, not a tuple
        result = service._render_template(
            "emails/order/order_completed.html", context
        )

        assert result is not None
        assert isinstance(result, str)

    def test_error_handling_for_template_rendering(self, service):
        """Test error handling during template rendering.

        A template no registered email type sends is rendered by the service
        itself; the registered ones go through ``emails.render``.
        """
        # Try to render with invalid context - patch at the module level where it's used
        with patch(
            "core.email.preview_service.render_to_string"
        ) as mock_render:
            mock_render.side_effect = Exception("Template error")

            preview = service.generate_preview(
                template_name="confirmation", order_id=None
            )

            # When rendering fails, error message should be in the content
            assert (
                "error" in preview.html_content.lower()
                or "template error" in preview.html_content.lower()
                or "error" in preview.text_content.lower()
                or "template error" in preview.text_content.lower()
            )

    def test_preview_dataclass_fields(self):
        """Test EmailPreview dataclass has correct fields."""
        preview = EmailPreview(
            template_name="test",
            subject="Test Subject",
            html_content="<html></html>",
            text_content="Text content",
            context_data={},
            order_id=None,
            is_sample_data=True,
            error=None,
        )

        assert preview.template_name == "test"
        assert preview.subject == "Test Subject"
        assert preview.html_content == "<html></html>"
        assert preview.text_content == "Text content"
        assert preview.context_data == {}
        assert preview.order_id is None
        assert preview.is_sample_data is True
        assert preview.error is None

    def test_preview_with_error(self):
        """Test EmailPreview with error."""
        preview = EmailPreview(
            template_name="test",
            subject="",
            html_content="Error occurred",
            text_content="Error occurred",
            context_data={},
            order_id=None,
            is_sample_data=True,
            error="Test error message",
        )

        assert preview.error == "Test error message"
        assert preview.html_content is not None
        assert preview.text_content is not None
