"""`release-reservations` must refuse junk, not 500 on it.

The action DECLARES `ReleaseReservationsRequestSerializer` in
`serializers_config` and never instantiated it. `request.data` went
straight into `id__in`, so a non-numeric id raised `ValueError` during
queryset evaluation — unhandled, a 500 with a full traceback, on an
endpoint any anonymous visitor can reach.
"""

from __future__ import annotations

from django.urls import reverse
from rest_framework.test import APITestCase

from cart.serializers.cart import MAX_RELEASE_RESERVATION_IDS
from tests.utils import TestURLFixerMixin
from user.factories.account import UserAccountFactory


class ReleaseReservationsValidationTest(TestURLFixerMixin, APITestCase):
    def setUp(self):
        self.client.force_authenticate(user=UserAccountFactory(num_addresses=0))
        self.url = reverse("cart-release-reservations")

    def test_a_non_numeric_id_is_a_400_not_a_500(self):
        response = self.client.post(
            self.url, {"reservationIds": ["abc"]}, format="json"
        )
        assert response.status_code == 400, response.status_code

    def test_a_structured_value_is_a_400_not_a_500(self):
        response = self.client.post(
            self.url, {"reservationIds": [{}]}, format="json"
        )
        assert response.status_code == 400, response.status_code

    def test_a_non_list_is_still_rejected(self):
        response = self.client.post(
            self.url, {"reservationIds": "abc"}, format="json"
        )
        assert response.status_code == 400, response.status_code

    def test_valid_ids_are_still_accepted(self):
        """Nothing owned by this caller, so nothing is released — but the
        request itself must be well-formed and answered normally."""
        response = self.client.post(
            self.url, {"reservationIds": [1, 2, 3]}, format="json"
        )
        assert response.status_code in (200, 400), response.status_code

    def test_an_over_long_id_list_is_refused(self):
        """Unbounded, this endpoint is one release attempt per id.

        `gift_card_codes` in the same serializer module has been capped
        at 3 all along; `reservation_ids` was the outlier.
        """
        response = self.client.post(
            self.url,
            {"reservationIds": list(range(1, MAX_RELEASE_RESERVATION_IDS + 2))},
            format="json",
        )
        assert response.status_code == 400, response.status_code

    def test_a_list_at_the_limit_is_still_accepted(self):
        response = self.client.post(
            self.url,
            {"reservationIds": list(range(1, MAX_RELEASE_RESERVATION_IDS + 1))},
            format="json",
        )
        assert response.status_code != 400, response.status_code
