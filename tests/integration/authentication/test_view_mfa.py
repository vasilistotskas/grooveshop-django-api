from allauth.mfa.models import Authenticator
from allauth.mfa.totp import generate_totp_secret
from allauth.mfa.totp import hotp_counter_from_time
from allauth.mfa.totp import hotp_value
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework.test import APITestCase


User = get_user_model()


class AuthenticateTotpAPIViewTestCase(APITestCase):
    def setUp(self):
        self.client = APIClient()

        self.user = User.objects.create_user(
            email="testuser@example.com", password="testpassword"
        )
        totp_secret = generate_totp_secret()
        self.authenticator = Authenticator.objects.create(
            user=self.user,
            type=Authenticator.Type.TOTP,
            data={"secret": totp_secret},
        )

    def test_unauthenticated_user(self):
        url = reverse("mfa_totp_authenticate")
        response = self.client.post(url, {"code": "123456"})
        self.assertEqual(response.status_code, 403)

    def test_authenticated_user_without_totp(self):
        self.client.login(email="testuser@example.com", password="testpassword")
        self.authenticator.delete()
        url = reverse("mfa_totp_authenticate")
        response = self.client.post(url, {"code": "123456"})
        self.assertEqual(response.status_code, 400)

    def test_authenticated_user_with_invalid_code(self):
        self.client.login(email="testuser@example.com", password="testpassword")
        url = reverse("mfa_totp_authenticate")
        response = self.client.post(url, {"secret": "invalid_code"})
        self.assertEqual(response.status_code, 400)

    def test_authenticated_user_with_valid_code(self):
        self.client.login(email="testuser@example.com", password="testpassword")
        counter = hotp_counter_from_time()
        valid_code = hotp_value(
            self.authenticator.wrap().instance.data["secret"], counter
        )
        url = reverse("mfa_totp_authenticate")
        response = self.client.post(url, {"code": valid_code})
        self.assertEqual(response.status_code, 200)

    def test_invalid_data(self):
        self.client.login(email="testuser@example.com", password="testpassword")
        url = reverse("mfa_totp_authenticate")
        response = self.client.post(url, {"invalid_key": "invalid_value"})
        self.assertEqual(response.status_code, 400)


class ActivateTotpAPIViewTestCase(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="testuser@example.com", password="testpassword"
        )
        totp_secret = generate_totp_secret()
        self.authenticator = Authenticator.objects.create(
            user=self.user, type=Authenticator.Type.TOTP, data={"secret": totp_secret}
        )

    def test_unauthenticated_user(self):
        url = reverse("mfa_totp_activate")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)
        response = self.client.post(url, {"code": "123456"})
        self.assertEqual(response.status_code, 403)

    def test_authenticated_user_with_totp(self):
        self.client.login(email="testuser@example.com", password="testpassword")
        url = reverse("mfa_totp_activate")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 400)
        response = self.client.post(url, {"code": "123456"})
        self.assertEqual(response.status_code, 400)

    def test_authenticated_user_without_totp_get(self):
        self.client.login(email="testuser@example.com", password="testpassword")
        self.authenticator.delete()
        url = reverse("mfa_totp_activate")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_authenticated_user_without_totp_post_valid_data(self):
        self.client.login(email="testuser@example.com", password="testpassword")
        self.authenticator.delete()
        url = reverse("mfa_totp_activate")
        response = self.client.post(url, {"code": "123456"})
        self.assertEqual(response.status_code, 200)

    def test_authenticated_user_without_totp_post_invalid_data(self):
        self.client.login(email="testuser@example.com", password="testpassword")
        self.authenticator.delete()
        url = reverse("mfa_totp_activate")
        response = self.client.post(url, {"invalid_key": "invalid_value"})
        self.assertEqual(response.status_code, 400)


class DeactivateTotpAPIViewTestCase(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="testuser@example.com", password="testpassword"
        )
        totp_secret = generate_totp_secret()
        self.authenticator = Authenticator.objects.create(
            user=self.user, type=Authenticator.Type.TOTP, data={"secret": totp_secret}
        )

    def test_unauthenticated_user(self):
        url = reverse("mfa_totp_deactivate")
        response = self.client.post(url)
        self.assertEqual(response.status_code, 403)

    def test_authenticated_user_without_totp(self):
        self.client.login(email="testuser@example.com", password="testpassword")
        self.authenticator.delete()
        url = reverse("mfa_totp_deactivate")
        response = self.client.post(url)
        self.assertEqual(response.status_code, 400)

    def test_authenticated_user_with_totp(self):
        self.client.login(email="testuser@example.com", password="testpassword")
        url = reverse("mfa_totp_deactivate")
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)


class GenerateRecoveryCodesAPIViewTestCase(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="testuser@example.com", password="testpassword"
        )

    def test_unauthenticated_user(self):
        url = reverse("mfa_recovery_codes_generate")
        response = self.client.post(url)
        self.assertEqual(response.status_code, 403)

    def test_authenticated_user(self):
        self.client.login(email="testuser@example.com", password="testpassword")
        url = reverse("mfa_recovery_codes_generate")
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)


class ViewRecoveryCodesAPIViewTestCase(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="testuser@example.com", password="testpassword"
        )
        totp_secret = generate_totp_secret()
        self.authenticator = Authenticator.objects.create(
            user=self.user,
            type=Authenticator.Type.RECOVERY_CODES,
            data={"seed": totp_secret, "used_mask": 0},
        )

    def test_unauthenticated_user(self):
        url = reverse("mfa_recovery_codes_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_authenticated_user_without_recovery_codes(self):
        self.client.login(email="testuser@example.com", password="testpassword")
        self.authenticator.delete()
        url = reverse("mfa_recovery_codes_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 400)

    def test_authenticated_user_with_recovery_codes(self):
        self.client.login(email="testuser@example.com", password="testpassword")
        url = reverse("mfa_recovery_codes_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)


class TotpActiveAPIViewTestCase(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="testuser@example.com", password="testpassword"
        )
        totp_secret = generate_totp_secret()
        self.authenticator = Authenticator.objects.create(
            user=self.user, type=Authenticator.Type.TOTP, data={"secret": totp_secret}
        )

    def test_unauthenticated_user(self):
        url = reverse("mfa_totp_active")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_authenticated_user_with_totp(self):
        self.client.login(email="testuser@example.com", password="testpassword")
        url = reverse("mfa_totp_active")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"active": True})

    def test_authenticated_user_without_totp(self):
        self.client.login(email="testuser@example.com", password="testpassword")
        self.authenticator.delete()
        url = reverse("mfa_totp_active")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"active": False})
