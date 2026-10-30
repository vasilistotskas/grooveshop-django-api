"""The low-stock email is built through ``build_email_context`` — the
SITE_LOGO_URL flow representative test for ``product/tasks.py`` (see
``core/utils/email_context.py``): the logo that reaches the sent mail is the
tenant's, or the platform's for the platform's own store.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core import mail

from product.factories.product import ProductFactory
from product.tasks import check_low_stock_products

LOGO = "https://cdn.example.com/tenant-logo.svg"


@pytest.fixture(autouse=True)
def _store_with_a_low_product():
    ProductFactory(
        active=True,
        num_images=0,
        num_reviews=0,
        stock=1,
        low_stock_threshold=5,
        low_stock_alert_sent=False,
    )
    mail.outbox.clear()
    with patch(
        "emails.recipients.tenant_operator_addresses",
        return_value=["admin@example.com"],
    ):
        yield


@pytest.mark.django_db
class TestLowStockAlertEmailContext:
    @patch("core.utils.email_context.tenant_logo_url", return_value=LOGO)
    def test_includes_tenant_logo_when_configured(self, _mock_logo_url):
        result = check_low_stock_products()

        assert result["alerted"] == 1
        [(html, _mimetype)] = mail.outbox[0].alternatives
        assert LOGO in html

    def test_falls_back_without_tenant_logo(self):
        result = check_low_stock_products()

        assert result["alerted"] == 1
        # No tenant is bound in this test — that context counts as PLATFORM
        # (see build_email_context._is_platform_tenant), so the platform
        # logo is used. Only an unbranded NON-platform tenant gets "".
        [(html, _mimetype)] = mail.outbox[0].alternatives
        assert "/static/logo-dark.svg" in html
