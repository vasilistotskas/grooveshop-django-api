from rest_framework import status
from rest_framework.test import APITestCase
from django.urls import reverse


class CSRFTestCase(APITestCase):
    def setUp(self):
        # If you need to set up anything before your tests run, do it here.
        pass

    def test_csrf_cookie_set(self):
        # We simulate a request from the frontend domain
        self.client.defaults["SERVER_NAME"] = "grooveshop.site"
        response = self.client.get(reverse("session"), HTTP_HOST="api.grooveshop.site")

        # Assert the CSRF cookie was set on the response
        self.assertIn("csrftoken", response.cookies)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_post_with_csrf(self):
        # First, we get a CSRF token by making a GET request
        self.client.defaults["SERVER_NAME"] = "grooveshop.site"
        get_response = self.client.get(
            reverse("session"), HTTP_HOST="api.grooveshop.site"
        )
        csrftoken = get_response.cookies["csrftoken"].value

        # Then, we use that token to make a POST request
        response = self.client.post(
            reverse("is_user_registered"),
            {"email": "testuser@example.com"},
            HTTP_HOST="api.grooveshop.site",
            HTTP_X_CSRFTOKEN=csrftoken,
        )

        # Assert that the POST request was successful (e.g., status code 201 for creation)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
