"""An order records where its shopper came from, on both creation paths.

``POST /order`` takes an optional ``attribution`` object (the
storefront's first-touch capture, or the agent gateway's protocol) and
the shopper's User-Agent header, and ``OrderAttributionService`` stores
one ``OrderAttribution`` row in the creation transaction — a ``direct``
row when nothing was sent. The order responses read it back.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from cart.factories.cart import CartFactory
from cart.factories.item import CartItemFactory
from country.factories import CountryFactory
from order.enum.attribution import OrderSourceType
from order.enum.status import PaymentStatus
from order.factories import OrderFactory
from order.models.attribution import OrderAttribution
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory
from region.factories import RegionFactory
from user.factories.account import UserAccountFactory

INSTAGRAM_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
    "Instagram 330.0.0.40.92 (iPhone14,5; iOS 17_4; el_GR; el)"
)


@pytest.mark.django_db
@patch(
    "order.services.OrderService.validate_shipping_address",
    return_value=None,
)
@patch(
    "order.services.OrderService.validate_cart_for_checkout",
    return_value={"valid": True, "errors": [], "warnings": []},
)
class TestOrderAttributionOnCreate(APITestCase):
    def setUp(self):
        super().setUp()
        # Outside a tenant there are no Stripe credentials at all; a
        # stand-in key lets the Stripe pay way count as configured.
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

        self.user = UserAccountFactory()
        self.country = CountryFactory()
        self.region = RegionFactory(country=self.country)
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

    def _post(self, pay_way, *, user_agent="", **extra):
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
            HTTP_USER_AGENT=user_agent,
        )

    def _cod(self):
        return PayWayFactory(
            provider_code="cod",
            settlement=PaySettlement.COURIER_CASH,
            active=True,
        )

    def test_the_offline_path_records_the_capture(self, *_mocks):
        response = self._post(
            self._cod(),
            user_agent=INSTAGRAM_UA,
            attribution={
                "utmSource": "ig",
                "utmMedium": "social",
                "utmCampaign": "Summer Drop",
                "clickIds": ["fbclid"],
                "referrer": "https://l.instagram.com/?u=https%3A%2F%2Fx",
                "landingPath": "/products/7?fbclid=IwAR0abc",
            },
        )

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        row = OrderAttribution.objects.get(order_id=response.json()["id"])
        assert (row.source_type, row.source) == (
            OrderSourceType.CAMPAIGN,
            "instagram",
        )
        assert row.medium == "social"
        assert row.campaign == "Summer Drop"
        assert row.referrer_host == "l.instagram.com"
        assert row.landing_path == "/products/7"
        assert response.json()["attribution"] == {
            "sourceType": "campaign",
            "source": "instagram",
            "medium": "social",
            "campaign": "Summer Drop",
        }

    def test_no_attribution_is_a_direct_row_not_a_refusal(self, *_mocks):
        response = self._post(self._cod())

        assert response.status_code == status.HTTP_201_CREATED
        row = OrderAttribution.objects.get(order_id=response.json()["id"])
        assert row.source_type == OrderSourceType.DIRECT
        assert row.source == ""

    def test_the_user_agent_alone_names_an_in_app_browser(self, *_mocks):
        response = self._post(
            self._cod(), user_agent=INSTAGRAM_UA, attribution={}
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["attribution"]["source"] == "instagram"
        assert response.json()["attribution"]["sourceType"] == "social"

    def test_the_agent_gateway_protocol_is_an_agent_order(self, *_mocks):
        response = self._post(self._cod(), attribution={"agentProtocol": "acp"})

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["attribution"]["sourceType"] == "agent"
        assert response.json()["attribution"]["source"] == "acp"

    def test_a_referrer_on_the_store_own_domain_is_ignored(self, *_mocks):
        with patch(
            "order.services.tenant_domain_set",
            return_value={"webside.gr"},
        ):
            response = self._post(
                self._cod(),
                attribution={"referrer": "https://www.webside.gr/cart"},
            )

        assert response.status_code == status.HTTP_201_CREATED
        row = OrderAttribution.objects.get(order_id=response.json()["id"])
        assert row.source_type == OrderSourceType.DIRECT
        assert row.referrer_host == ""

    def test_a_strange_referrer_does_not_fail_the_checkout(self, *_mocks):
        response = self._post(
            self._cod(),
            attribution={"referrer": "android-app://com.google.android.gm/"},
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["attribution"]["sourceType"] == "direct"

    def test_an_unknown_click_id_name_is_rejected(self, *_mocks):
        """``clickIds`` is a closed contract with the storefront, which
        sends only the names the schema enumerates."""
        response = self._post(self._cod(), attribution={"clickIds": ["utm_id"]})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not OrderAttribution.objects.exists()

    @patch("order.payment.get_payment_provider")
    def test_the_payment_first_path_records_it_too(
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
            pay_way,
            paymentIntentId="pi_test_attribution",
            attribution={"utmSource": "google", "clickIds": ["gclid"]},
        )

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        row = OrderAttribution.objects.get(order_id=response.json()["id"])
        assert (row.source_type, row.source) == (
            OrderSourceType.PAID,
            "google",
        )


@pytest.mark.django_db
class TestOrderAttributionOnRead(APITestCase):
    def setUp(self):
        super().setUp()
        self.user = UserAccountFactory()
        self.client.force_authenticate(user=self.user)

    def test_the_detail_reads_the_row_back(self):
        order = OrderFactory(user=self.user, num_order_items=0)
        OrderAttribution.objects.create(
            order=order,
            source_type=OrderSourceType.SEARCH,
            source="google",
        )

        response = self.client.get(
            reverse("order-detail", kwargs={"pk": order.pk})
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["attribution"] == {
            "sourceType": "search",
            "source": "google",
            "medium": "",
            "campaign": "",
        }

    def test_an_order_from_before_attribution_reads_null(self):
        order = OrderFactory(user=self.user, num_order_items=0)

        detail = self.client.get(
            reverse("order-detail", kwargs={"pk": order.pk})
        )
        listing = self.client.get(reverse("order-list"))

        assert detail.json()["attribution"] is None
        assert listing.json()["results"][0]["attribution"] is None


@pytest.mark.django_db
class TestOrderFilterBySource(APITestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(
            user=UserAccountFactory(is_staff=True, is_superuser=True)
        )
        self.instagram = OrderFactory(num_order_items=0)
        OrderAttribution.objects.create(
            order=self.instagram,
            source_type=OrderSourceType.SOCIAL,
            source="instagram",
        )
        self.campaign = OrderFactory(num_order_items=0)
        OrderAttribution.objects.create(
            order=self.campaign,
            source_type=OrderSourceType.CAMPAIGN,
            source="instagram",
        )
        self.google = OrderFactory(num_order_items=0)
        OrderAttribution.objects.create(
            order=self.google,
            source_type=OrderSourceType.SEARCH,
            source="google",
        )
        self.unattributed = OrderFactory(num_order_items=0)

    def _ids(self, **params):
        response = self.client.get(reverse("order-list"), params)
        assert response.status_code == status.HTTP_200_OK
        return {order["id"] for order in response.json()["results"]}

    def test_by_source_case_insensitively(self):
        assert self._ids(source="Instagram") == {
            self.instagram.id,
            self.campaign.id,
        }

    def test_by_source_type(self):
        assert self._ids(sourceType="search") == {self.google.id}

    def test_both_together(self):
        assert self._ids(source="instagram", sourceType="campaign") == {
            self.campaign.id
        }

    def test_an_unknown_source_type_is_refused(self):
        response = self.client.get(reverse("order-list"), {"sourceType": "tv"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
