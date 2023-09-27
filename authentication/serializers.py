from dj_rest_auth.registration.serializers import RegisterSerializer
from dj_rest_auth.serializers import JWTSerializer
from dj_rest_auth.serializers import JWTSerializerWithExpiration
from dj_rest_auth.serializers import LoginSerializer
from dj_rest_auth.serializers import PasswordChangeSerializer
from dj_rest_auth.serializers import PasswordResetConfirmSerializer
from dj_rest_auth.serializers import PasswordResetSerializer
from dj_rest_auth.serializers import TokenSerializer
from dj_rest_auth.serializers import UserDetailsSerializer
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


class AuthenticationPasswordResetSerializer(PasswordResetSerializer):
    pass


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
