from allauth_2fa.adapter import OTPAdapter
from django.conf import settings


class UserAccountAdapter(OTPAdapter):
    def get_email_confirmation_url(self, request, emailconfirmation):
        url = (
            settings.NUXT_BASE_URL
            + "/auth/registration/account-confirm-email/"
            + emailconfirmation.key
        )
        return url
