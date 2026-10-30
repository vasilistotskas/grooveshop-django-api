"""Regression test: the abandoned-cart email's unsubscribe link must use the
current signing-based token scheme.

The unsubscribe URL migration removed the old uidb64/default_token_generator
route; send_checkout_abandonment_emails still hand-rolled that old format,
producing links that always failed BadSignature. It now uses the shared
generate_blanket_unsubscribe_link helper.
"""

from __future__ import annotations

import re
from datetime import timedelta

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from cart.factories.cart import CartFactory
from emails import optout
from order.models.stock_reservation import StockReservation
from order.tasks import send_checkout_abandonment_emails
from product.factories.product import ProductFactory
from user.factories.account import UserAccountFactory


@override_settings(
    API_BASE_URL="https://api.test-site.com",
    DEFAULT_FROM_EMAIL="no-reply@test.com",
    INFO_EMAIL="info@test.com",
    SITE_NAME="GrooveShop",
)
class AbandonmentEmailUnsubscribeTestCase(TestCase):
    def test_unsubscribe_link_is_a_valid_signing_token(self):
        user = UserAccountFactory(email="shopper@example.com", num_addresses=0)
        cart = CartFactory(user=user, num_cart_items=0)
        product = ProductFactory(stock=5, num_images=0, num_reviews=0)

        reservation = StockReservation.objects.create(
            product=product,
            quantity=1,
            reserved_by=user,
            session_id=str(cart.uuid),
            expires_at=timezone.now() - timedelta(hours=5),
            consumed=True,
            abandonment_notified=False,
            order=None,
        )
        # updated_at is auto_now — force it past the abandonment cutoff.
        StockReservation.objects.filter(pk=reservation.pk).update(
            updated_at=timezone.now() - timedelta(hours=5)
        )

        mail.outbox = []
        sent = send_checkout_abandonment_emails()

        self.assertEqual(sent, 1)
        self.assertEqual(len(mail.outbox), 1)

        # RFC 8058 one-click: the header names this address and this type
        # under the pipeline's signed token (schema-scoped), not the dead
        # uidb64/reset-token format.
        header = mail.outbox[0].extra_headers["List-Unsubscribe"]
        match = re.search(r"<(https://[^>]+)>", header)
        self.assertIsNotNone(match)
        url = match.group(1)
        self.assertIn("/api/v1/emails/unsubscribe/", url)
        claims = optout.read_token(url.rstrip("/").rsplit("/", 1)[1])
        self.assertEqual(claims.address_hash, optout.address_hash(user.email))
        self.assertEqual(claims.type_key, "cart.checkout_abandoned")
