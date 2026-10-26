"""An order records the legal texts its buyer accepted, on both paths.

``POST /order`` requires ``termsAccepted``, and the creation
transaction stores one ``OrderLegalAcceptance`` per checkout legal
document (terms of use, privacy policy): the version in force in the
order's language and how it was accepted, the storefront's checkout
consent or an agent gateway checkout (proven by its shared secret). A
page with no published text in that language refuses the order.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from cart.factories.cart import CartFactory
from cart.factories.item import CartItemFactory
from country.factories import CountryFactory
from order.enum.create_error import OrderCreateErrorType
from order.enum.legal_acceptance import LegalAcceptanceSource
from order.enum.status import PaymentStatus
from order.models import Order, OrderLegalAcceptance
from page_config.models import ContentPage, ContentPageTranslation
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory
from region.factories import RegionFactory
from tests.utils.legal import publish_checkout_legal_documents
from tests.utils.shipping import enable_rate
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.usefixtures("trust_intent_binding")

TEXTS = {
    ("terms", "el"): ("Όροι χρήσης", "<p>Όροι</p>"),
    ("terms", "en"): ("Terms of use", "<p>Terms</p>"),
    ("privacy", "el"): ("Πολιτική απορρήτου", "<p>Απόρρητο</p>"),
    ("privacy", "en"): ("Privacy policy", "<p>Privacy</p>"),
}


@pytest.mark.django_db
@patch(
    "order.services.OrderService.validate_shipping_address",
    return_value=None,
)
@patch(
    "order.services.OrderService.validate_cart_for_checkout",
    return_value={"valid": True, "errors": [], "warnings": []},
)
class TestOrderLegalAcceptanceOnCreate(APITestCase):
    def setUp(self):
        super().setUp()
        patcher = patch(
            "tenant.credentials.stripe_credentials",
            return_value={
                "secret_key": "sk_test_dummy_tenant_key",
                "publishable_key": "pk_test_dummy_tenant_key",
                "live_mode": False,
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        self.pages = {}
        for (slug, language), (title, body) in TEXTS.items():
            page = self.pages.get(slug) or ContentPage.objects.create(
                slug=slug, is_published=True
            )
            self.pages[slug] = page
            ContentPageTranslation.objects.create(
                master=page, language_code=language, title=title, body=body
            )

        self.user = UserAccountFactory()
        self.country = CountryFactory()
        self.region = RegionFactory(country=self.country)
        enable_rate(self.country)
        self.cart = CartFactory(user=self.user)
        CartItemFactory(
            cart=self.cart,
            product=ProductFactory(
                active=True, stock=10, num_images=0, num_reviews=0
            ),
            quantity=1,
        )
        self.url = reverse("order-list")
        self.client.force_authenticate(user=self.user)

    def _post(self, pay_way, *, language="el", gateway_secret="", **extra):
        return self.client.post(
            self.url,
            {
                "payWayId": pay_way.id,
                "firstName": "Maria",
                "lastName": "Papadopoulou",
                "email": "maria@example.com",
                "street": "Ermou",
                "streetNumber": "1",
                "city": "Athens",
                "zipcode": "10563",
                "countryId": self.country.alpha_2,
                "regionId": self.region.alpha,
                "phone": "+306900000000",
                **extra,
            },
            format="json",
            HTTP_X_CART_ID=str(self.cart.uuid),
            HTTP_X_LANGUAGE=language,
            HTTP_X_INTERNAL_GATEWAY=gateway_secret,
        )

    def _cod(self):
        return PayWayFactory(
            provider_code="cod",
            settlement=PaySettlement.COURIER_CASH,
            active=True,
        )

    def _accepted(self, response):
        rows = OrderLegalAcceptance.objects.filter(
            order_id=response.json()["id"]
        ).select_related("document__page")
        return {
            row.document.page.slug: (
                row.document.language_code,
                row.document.title,
                row.document.body,
                row.source,
            )
            for row in rows
        }

    def test_the_checkout_consent_records_both_texts_in_force(self, *_mocks):
        response = self._post(self._cod(), termsAccepted=True)

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        consent = LegalAcceptanceSource.CHECKOUT_CONSENT
        assert self._accepted(response) == {
            "terms": ("el", "Όροι χρήσης", "<p>Όροι</p>", consent),
            "privacy": ("el", "Πολιτική απορρήτου", "<p>Απόρρητο</p>", consent),
        }

    def test_the_texts_are_those_of_the_order_language(self, *_mocks):
        response = self._post(self._cod(), language="en", termsAccepted=True)

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        accepted = self._accepted(response)
        assert accepted["terms"][:3] == ("en", "Terms of use", "<p>Terms</p>")
        assert accepted["privacy"][:3] == (
            "en",
            "Privacy policy",
            "<p>Privacy</p>",
        )

    def test_a_later_edit_does_not_change_what_the_order_accepted(
        self, *_mocks
    ):
        response = self._post(self._cod(), termsAccepted=True)
        translation = ContentPageTranslation.objects.get(
            master=self.pages["terms"], language_code="el"
        )
        translation.body = "<p>Νέοι όροι</p>"
        translation.save()

        assert self._accepted(response)["terms"][2] == "<p>Όροι</p>"

    @patch("order.payment.get_payment_provider")
    def test_the_payment_first_path_records_them_too(
        self, mock_get_provider, *_mocks
    ):
        provider = MagicMock()
        provider.get_payment_status.return_value = (
            PaymentStatus.COMPLETED,
            {},
        )
        mock_get_provider.return_value = provider
        pay_way = PayWayFactory(
            provider_code="stripe",
            settlement=PaySettlement.ONLINE,
            active=True,
        )

        response = self._post(
            pay_way, paymentIntentId="pi_test_legal", termsAccepted=True
        )

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        assert set(self._accepted(response)) == {"terms", "privacy"}

    @override_settings(AGENT_GATEWAY_INTERNAL_SECRET="gateway-secret")
    def test_an_agent_gateway_order_is_an_agent_checkout(self, *_mocks):
        response = self._post(
            self._cod(), gateway_secret="gateway-secret", termsAccepted=True
        )

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        sources = {row[3] for row in self._accepted(response).values()}
        assert sources == {LegalAcceptanceSource.AGENT_CHECKOUT}

    @override_settings(AGENT_GATEWAY_INTERNAL_SECRET="gateway-secret")
    def test_a_wrong_gateway_secret_is_the_checkout_consent(self, *_mocks):
        response = self._post(
            self._cod(), gateway_secret="guess", termsAccepted=True
        )

        sources = {row[3] for row in self._accepted(response).values()}
        assert sources == {LegalAcceptanceSource.CHECKOUT_CONSENT}

    def test_terms_refused_is_no_order(self, *_mocks):
        response = self._post(self._cod(), termsAccepted=False)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        body = response.json()
        assert body["fields"]["termsAccepted"][0]["code"] == (
            "terms_not_accepted"
        )
        assert not Order.objects.exists()

    def test_a_text_missing_in_the_order_language_refuses_the_order(
        self, *_mocks
    ):
        ContentPageTranslation.objects.filter(
            master=self.pages["privacy"], language_code="en"
        ).delete()

        response = self._post(self._cod(), language="en", termsAccepted=True)

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json()["code"] == (
            OrderCreateErrorType.LEGAL_DOCUMENT_UNAVAILABLE
        )
        assert response.json()["error"]["document"] == "privacy"
        assert not Order.objects.exists()
        assert not OrderLegalAcceptance.objects.exists()

    @patch("order.payment.get_payment_provider")
    def test_a_missing_text_refuses_before_the_payment_is_looked_up(
        self, mock_get_provider, *_mocks
    ):
        ContentPageTranslation.objects.filter(
            master=self.pages["terms"], language_code="el"
        ).delete()
        pay_way = PayWayFactory(
            provider_code="stripe",
            settlement=PaySettlement.ONLINE,
            active=True,
        )

        response = self._post(
            pay_way, paymentIntentId="pi_test_refused", termsAccepted=True
        )

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json()["error"]["document"] == "terms"
        mock_get_provider.assert_not_called()
        assert not Order.objects.exists()

    def test_no_order_without_the_consent(self, *_mocks):
        response = self._post(self._cod())

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["fields"]["termsAccepted"][0]["code"] == (
            "required"
        )
        assert not Order.objects.exists()


@pytest.mark.django_db
class TestPaymentIntentNeedsTheLegalTexts(APITestCase):
    """A card is confirmed against the cart's payment intent before the
    order exists, so the intent is refused while a checkout legal text
    cannot be shown: no money moves for an order Django would refuse."""

    def setUp(self):
        super().setUp()
        self.user = UserAccountFactory(num_addresses=0)
        self.cart = CartFactory(user=self.user, num_cart_items=1)
        self.client.force_authenticate(user=self.user)
        self.country = CountryFactory()
        enable_rate(self.country)
        self.pay_way = PayWayFactory.create_online_payment(
            provider_code="stripe", active=True
        )

    def _post(self, language="el"):
        provider = MagicMock()
        provider.process_payment.return_value = (
            True,
            {"client_secret": "cs_test_legal", "payment_id": "pi_test_legal"},
        )
        with patch(
            "pay_way.services.PayWayService.get_provider_for_pay_way",
            return_value=provider,
        ):
            response = self.client.post(
                reverse("cart-create-payment-intent"),
                {
                    "pay_way_id": self.pay_way.id,
                    "shipping_kind": "home_delivery",
                    "country_id": self.country.alpha_2,
                },
                format="json",
                HTTP_X_LANGUAGE=language,
            )
        return response, provider

    def test_refused_without_a_text_in_the_language(self):
        response, provider = self._post()

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json()["code"] == (
            OrderCreateErrorType.LEGAL_DOCUMENT_UNAVAILABLE
        )
        provider.process_payment.assert_not_called()

    def test_minted_once_both_texts_are_published(self):
        publish_checkout_legal_documents()

        response, provider = self._post()

        assert response.status_code == status.HTTP_200_OK, response.json()
        provider.process_payment.assert_called_once()
