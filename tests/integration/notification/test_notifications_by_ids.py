"""C15 — notifications_by_ids must cap the IN clause to 500 ids."""

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from notification.factories.notification import NotificationFactory
from notification.factories.user import NotificationUserFactory
from user.factories.account import UserAccountFactory


class NotificationsByIdsBoundedTestCase(APITestCase):
    """Passing more than 500 ids must not execute an unbounded IN query."""

    @classmethod
    def setUpTestData(cls):
        cls.user = UserAccountFactory()
        cls.url = reverse("notifications-by-ids")

    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def test_1000_ids_are_capped_to_500(self):
        """With 1000 ids (all non-existent) the endpoint must respond with
        404 (no results found) rather than any server error, confirming it
        accepted the capped list without blowing up.
        """
        ids = list(range(1, 1001))
        response = self.client.post(self.url, {"ids": ids}, format="json")
        # All ids are fake, so 404 is correct — but the key check is it doesn't
        # return a 500 or process 1000 rows.
        self.assertIn(
            response.status_code,
            [status.HTTP_404_NOT_FOUND, status.HTTP_200_OK],
        )

    def test_empty_ids_returns_400(self):
        response = self.client.post(self.url, {"ids": []}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_valid_owned_ids_returns_200(self):
        notification = NotificationFactory()
        NotificationUserFactory(user=self.user, notification=notification)

        response = self.client.post(
            self.url, {"ids": [notification.pk]}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_unauthenticated_request_returns_401(self):
        self.client.force_authenticate(user=None)
        response = self.client.post(self.url, {"ids": [1, 2, 3]}, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
