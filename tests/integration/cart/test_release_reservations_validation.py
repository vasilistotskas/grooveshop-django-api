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
        """200, not merely "not 400".

        `!= 400` would also pass on a 500 or a 403, so it would hide a
        broken endpoint rather than catch one. Ids matching nothing are
        not an error here — the action releases what the caller owns and
        reports the count.
        """
        response = self.client.post(
            self.url,
            {"reservationIds": list(range(1, MAX_RELEASE_RESERVATION_IDS + 1))},
            format="json",
        )
        assert response.status_code == 200, response.status_code

    def test_the_refusal_says_what_was_actually_wrong(self):
        """A list of 101 valid integers is not "not a list of integers"."""
        response = self.client.post(
            self.url,
            {"reservationIds": list(range(1, MAX_RELEASE_RESERVATION_IDS + 2))},
            format="json",
        )

        assert response.status_code == 400
        assert "no more than" in response.data["detail"], response.data


class ReleaseAlreadyConsumedTest(TestURLFixerMixin, APITestCase):
    """An already-consumed reservation is a no-op, not a failure.

    Placing an order CONSUMES its reservations, and the checkout then
    releases the ids it was holding. On every offline order that raced:
    the endpoint answered 200 carrying ``failed_releases``, the
    storefront read that as a failure and stacked an error toast on top
    of the success one, and the shopper never reached the success page
    (order #274, 2026-09-10).

    Releasing is idempotent by contract — the caller is asking for "this
    reservation is not holding stock", which is already true. Reporting
    that as an error is what broke checkout.
    """

    def setUp(self):
        from cart.factories import CartFactory
        from product.factories.product import ProductFactory

        self.user = UserAccountFactory(num_addresses=0)
        self.client.force_authenticate(user=self.user)
        self.url = reverse("cart-release-reservations")
        self.cart = CartFactory(user=self.user)
        self.product = ProductFactory(num_images=0)

    def _reservation(self, *, consumed: bool):
        from datetime import timedelta

        from django.utils import timezone

        from order.models import StockReservation

        return StockReservation.objects.create(
            product=self.product,
            quantity=1,
            reserved_by=self.user,
            session_id=str(self.cart.uuid),
            consumed=consumed,
            expires_at=timezone.now() + timedelta(minutes=15),
        )

    def test_a_consumed_reservation_reports_no_failure(self):
        reservation = self._reservation(consumed=True)

        response = self.client.post(
            self.url, {"reservationIds": [reservation.id]}, format="json"
        )

        assert response.status_code == 200, response.status_code
        # The storefront keys its error toast off this being present.
        assert "failedReleases" not in response.json(), response.json()

    def test_a_consumed_reservation_counts_as_released(self):
        reservation = self._reservation(consumed=True)

        response = self.client.post(
            self.url, {"reservationIds": [reservation.id]}, format="json"
        )

        assert response.json()["releasedCount"] == 1

    def test_an_active_reservation_is_still_actually_released(self):
        """The no-op path must not swallow real work."""
        from order.models import StockReservation

        reservation = self._reservation(consumed=False)

        response = self.client.post(
            self.url, {"reservationIds": [reservation.id]}, format="json"
        )

        assert response.status_code == 200
        reservation.refresh_from_db()
        assert reservation.consumed is True
        assert StockReservation.objects.get(pk=reservation.pk).consumed

    def test_an_unowned_reservation_is_still_refused(self):
        """The idempotent path must not become an IDOR."""
        other = UserAccountFactory(num_addresses=0)
        from datetime import timedelta

        from django.utils import timezone

        from order.models import StockReservation

        foreign = StockReservation.objects.create(
            product=self.product,
            quantity=1,
            reserved_by=other,
            session_id="someone-elses-cart",
            consumed=True,
            expires_at=timezone.now() + timedelta(minutes=15),
        )

        response = self.client.post(
            self.url, {"reservationIds": [foreign.id]}, format="json"
        )

        assert response.status_code == 200
        assert response.json()["releasedCount"] == 0
        assert response.json()["failedReleases"]
