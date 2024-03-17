from allauth.account.models import EmailAddress
from allauth.account.views import ConfirmEmailView
from allauth.account.views import EmailVerificationSentView
from dj_rest_auth.registration.views import RegisterView
from dj_rest_auth.registration.views import ResendEmailVerificationView
from dj_rest_auth.registration.views import VerifyEmailView
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.response import Response

from core.api.parsers import NoUnderscoreBeforeNumberCamelCaseJSONParser

User = get_user_model()


class AuthRegisterView(RegisterView):
    parser_classes = [NoUnderscoreBeforeNumberCamelCaseJSONParser]

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
    pass


class AuthResendEmailVerificationView(ResendEmailVerificationView):
    pass


class AuthConfirmEmailView(ConfirmEmailView):
    pass


class AuthEmailVerificationSentView(EmailVerificationSentView):
    pass
