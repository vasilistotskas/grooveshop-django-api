"""Product-alert emails carry Gmail/Yahoo-compliant unsubscribe headers.

The alerts are marketing mail (``emails.types.Kind.MARKETING``), so the
pipeline gives every one the RFC 8058 one-click ``List-Unsubscribe`` headers,
a guest's alert (an address with no account) included: the opt-out token
names the address, not an account.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest
from django.core import mail

from emails import optout
from product.factories.product import ProductFactory
from product.models.alert import ProductAlert, ProductAlertKind
from product.tasks import send_product_alert_restock
from tests.utils.store_settings import switch_store_settings
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _alerts_on(bind_tenant):
    switch_store_settings(PRODUCT_ALERTS_ENABLED=True)
    mail.outbox.clear()


def _send(**alert_fields) -> mail.EmailMessage:
    product = ProductFactory(stock=5, num_images=0, num_reviews=0)
    ProductAlert.objects.create(
        kind=ProductAlertKind.RESTOCK, product=product, **alert_fields
    )
    mail.outbox.clear()
    send_product_alert_restock.run(product.pk)
    [message] = mail.outbox
    return message


class TestProductAlertUnsubscribeHeaders:
    @pytest.fixture(autouse=True)
    def _hosts(self, settings):
        settings.API_BASE_URL = "https://api.example.com"
        settings.NUXT_BASE_URL = "https://example.com"

    def test_an_alert_to_a_member_is_one_click_unsubscribable(self):
        user = UserAccountFactory(num_addresses=0)

        headers = _send(user=user).extra_headers

        # RFC 8058: the https form is signed for this address and type.
        url = headers["List-Unsubscribe"].split("<")[-1].rstrip(">")
        token = urlsplit(url).path.rsplit("/", 1)[-1]
        claims = optout.read_token(token)
        assert claims.address_hash == optout.address_hash(user.email)
        assert claims.type_key == "product.restock_alert"
        assert url.startswith(
            "https://api.example.com/api/v1/emails/unsubscribe/"
        )
        assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
        assert headers["List-ID"] == "<product-restock-alerts.example.com>"

    def test_an_alert_to_a_guest_is_too(self):
        headers = _send(email="anon@example.com").extra_headers

        claims = optout.read_token(
            headers["List-Unsubscribe"]
            .split("<")[-1]
            .rstrip(">")
            .rsplit("/", 1)[-1]
        )
        assert claims.address_hash == optout.address_hash("anon@example.com")
        assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"

    def test_a_guest_who_opted_out_is_not_written_to(self):
        optout.opt_out(
            "anon@example.com",
            "product.restock_alert",
            source=optout.EmailOptOut.Source.ONE_CLICK,
        )
        product = ProductFactory(stock=5, num_images=0, num_reviews=0)
        alert = ProductAlert.objects.create(
            kind=ProductAlertKind.RESTOCK,
            product=product,
            email="anon@example.com",
        )
        mail.outbox.clear()

        result = send_product_alert_restock.run(product.pk)

        assert mail.outbox == []
        assert result["sent"] == 0
        alert.refresh_from_db()
        assert alert.is_active is False
