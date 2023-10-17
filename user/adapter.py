from allauth.account.adapter import DefaultAccountAdapter
from django.conf import settings


class UserAccountAdapter(DefaultAccountAdapter):
    def get_email_confirmation_url(self, request, emailconfirmation):
        url = (
            settings.NUXT_BASE_URL
            + "/auth/registration/account-confirm-email/"
            + emailconfirmation.key
        )
        return url
