"""Tests that ``check_low_stock_products`` routes its render context
through ``build_email_context`` — the SITE_LOGO_URL flow representative
test for ``product/tasks.py`` (see ``core/utils/email_context.py``).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import override_settings

from product.factories.product import ProductFactory
from product.tasks import check_low_stock_products


@pytest.mark.django_db
class TestLowStockAlertEmailContext:
    @override_settings(INFO_EMAIL="admin@example.com")
    @patch("product.tasks.EmailMultiAlternatives")
    @patch("product.tasks.render_to_string")
    @patch(
        "core.utils.email_context.tenant_logo_url",
        return_value="https://cdn.example.com/tenant-logo.svg",
    )
    def test_includes_tenant_logo_when_configured(
        self, _mock_logo_url, mock_render, mock_email_cls
    ):
        ProductFactory(
            active=True,
            num_images=0,
            num_reviews=0,
            stock=1,
            low_stock_threshold=5,
            low_stock_alert_sent=False,
        )
        mock_render.return_value = "<html>Low stock</html>"

        result = check_low_stock_products()

        assert result["alerted"] == 1
        rendered_context = mock_render.call_args_list[0][0][1]
        assert (
            rendered_context["SITE_LOGO_URL"]
            == "https://cdn.example.com/tenant-logo.svg"
        )

    @override_settings(INFO_EMAIL="admin@example.com")
    @patch("product.tasks.EmailMultiAlternatives")
    @patch("product.tasks.render_to_string")
    def test_falls_back_without_tenant_logo(self, mock_render, mock_email_cls):
        ProductFactory(
            active=True,
            num_images=0,
            num_reviews=0,
            stock=1,
            low_stock_threshold=5,
            low_stock_alert_sent=False,
        )
        mock_render.return_value = "<html>Low stock</html>"

        result = check_low_stock_products()

        assert result["alerted"] == 1
        rendered_context = mock_render.call_args_list[0][0][1]
        assert rendered_context["SITE_LOGO_URL"] == ""
