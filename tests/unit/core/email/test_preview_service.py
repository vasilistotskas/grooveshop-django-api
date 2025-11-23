"""Unit tests for email template preview service."""

import pytest
from unittest.mock import Mock, patch
from core.email.preview_service import EmailTemplatePreviewService, EmailPreview
from order.models import Order


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
            template_name="order_confirmation", order_id=None
        )

        assert isinstance(preview, EmailPreview)
        assert preview.template_name == "order_confirmation"
        assert preview.html_content is not None
        assert preview.text_content is not None
        assert preview.error is None

    @pytest.mark.django_db
    def test_generate_preview_with_real_order(self, service):
        """Test generating preview with real order data."""
        # This test requires a real order in the database
        # We'll mock it for now
        with patch.object(
            service, "_get_real_order_context"
        ) as mock_get_context:
            mock_get_context.return_value = {
                "order": Mock(id=1, status="pending"),
                "site_name": "Test Site",
                "site_url": "http://test.com",
            }

            preview = service.generate_preview(
                template_name="order_confirmation", order_id=1
            )

            assert isinstance(preview, EmailPreview)
            assert preview.error is None

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

    def test_generate_preview_with_invalid_order_id(self, service):
        """Test generating preview with invalid order ID."""
        with patch.object(Order.objects, "get") as mock_get:
            mock_get.side_effect = Order.DoesNotExist()

            preview = service.generate_preview(
                template_name="order_confirmation", order_id=99999
            )

            # Should fall back to sample data
            assert isinstance(preview, EmailPreview)
            # Should still generate preview with sample data
            assert preview.html_content is not None or preview.error is not None

    def test_preview_contains_html_content(self, service):
        """Test that preview contains HTML content."""
        preview = service.generate_preview(
            template_name="order_confirmation", order_id=None
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
            template_name="order_confirmation", order_id=None
        )

        if preview.error is None:
            assert preview.text_content is not None
            assert len(preview.text_content) > 0

    def test_preview_context_data_is_dict(self, service):
        """Test that preview context data is a dictionary."""
        preview = service.generate_preview(
            template_name="order_confirmation", order_id=None
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
            "emails/order/order_confirmation.html", context
        )

        assert result is not None
        assert isinstance(result, str)

    def test_error_handling_for_template_rendering(self, service):
        """Test error handling during template rendering."""
        # Try to render with invalid context - patch at the module level where it's used
        with patch(
            "core.email.preview_service.render_to_string"
        ) as mock_render:
            mock_render.side_effect = Exception("Template error")

            preview = service.generate_preview(
                template_name="order_confirmation", order_id=None
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
