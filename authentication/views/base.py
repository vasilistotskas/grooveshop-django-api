from allauth.socialaccount.models import SocialAccount
from dj_rest_auth.registration.views import SocialAccountDisconnectView
from dj_rest_auth.registration.views import SocialAccountListView
from dj_rest_auth.views import LoginView
from dj_rest_auth.views import LogoutView
from dj_rest_auth.views import PasswordChangeView
from dj_rest_auth.views import PasswordResetConfirmView
from dj_rest_auth.views import PasswordResetView
from dj_rest_auth.views import UserDetailsView
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from core.api.parsers import NoUnderscoreBeforeNumberCamelCaseJSONParser

User = get_user_model()


class AuthPasswordResetView(PasswordResetView):
    pass


class AuthPasswordResetConfirmView(PasswordResetConfirmView):
    parser_classes = [NoUnderscoreBeforeNumberCamelCaseJSONParser]


class AuthLoginView(LoginView):
    pass


class AuthLogoutView(LogoutView):
    serializer_class = None


class AuthPasswordChangeView(PasswordChangeView):
    parser_classes = [NoUnderscoreBeforeNumberCamelCaseJSONParser]


class AuthUserDetailsView(UserDetailsView):
    pass


class AuthSocialAccountListView(SocialAccountListView):
    pagination_class = None

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return SocialAccount.objects.none()
        return SocialAccount.objects.filter(user=self.request.user)


class AuthSocialAccountDisconnectView(SocialAccountDisconnectView):
    pass


@extend_schema(
    description=_("Returns if the user is already registered or not."),
    request=None,
    responses={"registered": bool},
    methods=["POST"],
)
@api_view(["POST"])
def is_user_registered(request: Request):
    email = request.data.get("email", None)
    if not email:
        return Response({"registered": False}, status=200)
    if User.objects.filter(email=email).exists():
        return Response({"registered": True}, status=200)
    return Response({"registered": False}, status=200)
