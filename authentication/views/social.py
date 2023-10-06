from allauth.account.models import EmailAddress
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.providers.facebook.views import FacebookOAuth2Adapter
from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
from allauth.socialaccount.providers.oauth2.client import OAuth2Client
from dj_rest_auth.registration.views import SocialConnectView
from dj_rest_auth.registration.views import SocialLoginView
from django.conf import settings
from django.contrib.auth import get_user_model

User = get_user_model()


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    def pre_social_login(self, request, sociallogin):
        email = None

        # Ignore existing social accounts, just do this stuff for new ones.
        if sociallogin.is_existing:
            return

        # some social logins don't have an email address, e.g. facebook accounts
        # with mobile numbers only, but allauth takes care of this case so just
        # ignore it.
        # we are looking for email in two places here:
        if "email" in sociallogin.account.extra_data:
            email = sociallogin.account.extra_data["email"].lower()

        for email_address in sociallogin.email_addresses:
            if email_address.verified:
                email = email_address
                break

        if not email:
            return

        # check if given email address already exists.
        # Note: __iexact is used to ignore cases
        try:
            # email_address = EmailAddress.objects.get(email__iexact=email)
            user = User.objects.get(email=email)
            if user:
                pass
            else:
                return

        # if it does not, let allauth take care of this new social account.
        except EmailAddress.DoesNotExist:
            return

        # if it does, connect this new social login to the existing user.
        user = user
        sociallogin.connect(request, user)


class FacebookLogin(SocialLoginView):
    authentication_classes = []
    adapter_class = FacebookOAuth2Adapter


class GoogleLogin(SocialLoginView):
    authentication_classes = []
    adapter_class = GoogleOAuth2Adapter
    callback_url = settings.GOOGLE_CALLBACK_URL
    client_class = OAuth2Client


class FacebookConnect(SocialConnectView):
    authentication_classes = []
    adapter_class = FacebookOAuth2Adapter


class GoogleConnect(SocialConnectView):
    authentication_classes = []
    adapter_class = GoogleOAuth2Adapter
    callback_url = settings.GOOGLE_CALLBACK_URL
    client_class = OAuth2Client
