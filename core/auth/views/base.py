from allauth.socialaccount.models import SocialAccount
from dj_rest_auth.registration.views import SocialAccountDisconnectView
from dj_rest_auth.registration.views import SocialAccountListView
from dj_rest_auth.views import LoginView
from dj_rest_auth.views import LogoutView
from dj_rest_auth.views import PasswordChangeView
from dj_rest_auth.views import PasswordResetConfirmView
from dj_rest_auth.views import PasswordResetView
from dj_rest_auth.views import UserDetailsView

from core.api.parsers import NoUnderscoreBeforeNumberCamelCaseJSONParser


class AuthPasswordResetView(PasswordResetView):
    pass


class AuthPasswordResetConfirmView(PasswordResetConfirmView):
    parser_classes = (NoUnderscoreBeforeNumberCamelCaseJSONParser,)


class AuthLoginView(LoginView):
    pass


class AuthLogoutView(LogoutView):
    serializer_class = None
    pass


class AuthPasswordChangeView(PasswordChangeView):
    parser_classes = (NoUnderscoreBeforeNumberCamelCaseJSONParser,)


class AuthUserDetailsView(UserDetailsView):
    pass


class AuthSocialAccountListView(SocialAccountListView):
    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return SocialAccount.objects.none()
        return SocialAccount.objects.filter(user=self.request.user)


class AuthSocialAccountDisconnectView(SocialAccountDisconnectView):
    pass
