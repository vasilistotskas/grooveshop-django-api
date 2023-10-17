from allauth.account.adapter import get_adapter
from allauth.account.forms import default_token_generator
from allauth.account.utils import user_username
from dj_rest_auth.forms import AllAuthPasswordResetForm
from dj_rest_auth.forms import default_url_generator
from dj_rest_auth.registration.serializers import RegisterSerializer
from dj_rest_auth.serializers import JWTSerializer
from dj_rest_auth.serializers import JWTSerializerWithExpiration
from dj_rest_auth.serializers import LoginSerializer
from dj_rest_auth.serializers import PasswordChangeSerializer
from dj_rest_auth.serializers import PasswordResetConfirmSerializer
from dj_rest_auth.serializers import PasswordResetSerializer
from dj_rest_auth.serializers import TokenSerializer
from dj_rest_auth.serializers import UserDetailsSerializer
from django.conf import settings
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.sites.shortcuts import get_current_site
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer


class AuthenticationLoginSerializer(LoginSerializer):
    username = None  # Remove the username field


class AuthenticationTokenSerializer(TokenSerializer):
    pass


class AuthenticationJWTSerializer(JWTSerializer):
    pass


class AuthenticationJWTSerializerWithExpiration(JWTSerializerWithExpiration):
    pass


class AuthenticationTokenObtainPairSerializer(TokenObtainPairSerializer):
    pass


class AuthenticationSerializer(UserDetailsSerializer):
    class Meta(UserDetailsSerializer.Meta):
        fields = (
            "id",
            "email",
        )


class AuthenticationAllAuthPasswordResetForm(AllAuthPasswordResetForm):
    def save(self, request, **kwargs):
        current_site = get_current_site(request)
        email = self.cleaned_data["email"]
        token_generator = kwargs.get("token_generator", default_token_generator)

        for user in self.users:
            temp_key = token_generator.make_token(user)

            # send the password reset email
            url_generator = kwargs.get("url_generator", default_url_generator)
            url = url_generator(request, user, temp_key)

            # replace the url domain with the nuxt domain
            domain = settings.NUXT_BASE_DOMAIN
            url = url.replace(url.split("/")[2], domain)

            context = {
                "current_site": current_site,
                "user": user,
                "password_reset_url": url,
                "request": request,
            }
            if settings.ACCOUNT_AUTHENTICATION_METHOD != "email":
                context["username"] = user_username(user)
            get_adapter(request).send_mail(
                "account/email/password_reset_key", email, context
            )
        return self.cleaned_data["email"]


class AuthenticationPasswordResetSerializer(PasswordResetSerializer):
    @property
    def password_reset_form_class(self):
        if "allauth" in settings.INSTALLED_APPS:
            return AuthenticationAllAuthPasswordResetForm
        else:
            return PasswordResetForm


class AuthenticationPasswordResetConfirmSerializer(PasswordResetConfirmSerializer):
    pass


class AuthenticationPasswordChangeSerializer(PasswordChangeSerializer):
    pass


class AuthenticationRegisterSerializer(RegisterSerializer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop("username")

    def get_cleaned_data(self):
        return {
            "password1": self.validated_data.get("password1", ""),
            "email": self.validated_data.get("email", ""),
        }


# MFA
class AuthenticateTOTPSerializer(serializers.Serializer):
    code = serializers.CharField()


class ActivateTOTPSerializer(serializers.Serializer):
    code = serializers.CharField(label=_("Authenticator code"))


class DeactivateTOTPSerializer(serializers.Serializer):
    pass


class RecoveryCodeSerializer(serializers.Serializer):
    pass


class TOTPEnabledSerializer(serializers.Serializer):
    enabled = serializers.BooleanField(label=_("TOTP enabled"))
