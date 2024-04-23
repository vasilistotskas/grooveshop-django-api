from allauth.account.models import EmailAddress
from allauth.account.views import ConfirmEmailView
from allauth.account.views import EmailVerificationSentView
from dj_rest_auth.registration.views import RegisterView
from dj_rest_auth.registration.views import ResendEmailVerificationView
from dj_rest_auth.registration.views import VerifyEmailView
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.decorators import throttle_classes
from rest_framework.response import Response

from core.api.parsers import NoUnderscoreBeforeNumberCamelCaseJSONParser
from core.api.throttling import BurstRateThrottle

User = get_user_model()


class AuthRegisterView(RegisterView):
    parser_classes = [NoUnderscoreBeforeNumberCamelCaseJSONParser]

    @throttle_classes([BurstRateThrottle])
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data.get("email")
        email_address_obj = EmailAddress.objects.filter(email=email).first()

        if email_address_obj:
            if not email_address_obj.verified:
                return Response(
                    {
                        "error": "This email already exists but is not verified. Please verify your email."
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        return super().create(request, *args, **kwargs)


class AuthVerifyEmailView(VerifyEmailView):
    @throttle_classes([BurstRateThrottle])
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class AuthResendEmailVerificationView(ResendEmailVerificationView):
    @throttle_classes([BurstRateThrottle])
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)


class AuthConfirmEmailView(ConfirmEmailView):
    pass


class AuthEmailVerificationSentView(EmailVerificationSentView):
    pass
