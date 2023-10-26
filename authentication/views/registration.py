from allauth.account.views import ConfirmEmailView
from allauth.account.views import EmailVerificationSentView
from dj_rest_auth.registration.views import RegisterView
from dj_rest_auth.registration.views import ResendEmailVerificationView
from dj_rest_auth.registration.views import VerifyEmailView

from core.api.parsers import NoUnderscoreBeforeNumberCamelCaseJSONParser


class AuthRegisterView(RegisterView):
    parser_classes = [NoUnderscoreBeforeNumberCamelCaseJSONParser]


class AuthVerifyEmailView(VerifyEmailView):
    pass


class AuthResendEmailVerificationView(ResendEmailVerificationView):
    pass


class AuthConfirmEmailView(ConfirmEmailView):
    pass


class AuthEmailVerificationSentView(EmailVerificationSentView):
    pass
