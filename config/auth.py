from datetime import timedelta
from os import getenv

DEBUG = getenv("DEBUG", "True") == "True"

NUXT_BASE_URL = getenv("NUXT_BASE_URL", "http://localhost:3000")

REST_AUTH = {
    "LOGIN_SERIALIZER": "authentication.serializers.AuthenticationLoginSerializer",
    "TOKEN_SERIALIZER": "authentication.serializers.AuthenticationTokenSerializer",
    "JWT_SERIALIZER": "authentication.serializers.AuthenticationJWTSerializer",
    "JWT_SERIALIZER_WITH_EXPIRATION": "authentication.serializers.AuthenticationJWTSerializerWithExpiration",
    "JWT_TOKEN_CLAIMS_SERIALIZER": "authentication.serializers.AuthenticationTokenObtainPairSerializer",
    "USER_DETAILS_SERIALIZER": "authentication.serializers.AuthenticationSerializer",
    "PASSWORD_RESET_SERIALIZER": "authentication.serializers.AuthenticationPasswordResetSerializer",
    "PASSWORD_RESET_CONFIRM_SERIALIZER": "authentication.serializers.AuthenticationPasswordResetConfirmSerializer",
    "PASSWORD_CHANGE_SERIALIZER": "authentication.serializers.AuthenticationPasswordChangeSerializer",
    "REGISTER_SERIALIZER": "authentication.serializers.AuthenticationRegisterSerializer",
    "REGISTER_PERMISSION_CLASSES": ("rest_framework.permissions.AllowAny",),
    "TOKEN_MODEL": "rest_framework.authtoken.models.Token",
    "TOKEN_CREATOR": "dj_rest_auth.utils.default_create_token",
    "PASSWORD_RESET_USE_SITES_DOMAIN": False,
    "OLD_PASSWORD_FIELD_ENABLED": False,
    "LOGOUT_ON_PASSWORD_CHANGE": False,
    "SESSION_LOGIN": True,
    "USE_JWT": True,
    "JWT_AUTH_COOKIE": "jwt_auth",
    "JWT_AUTH_REFRESH_COOKIE": "jwt_refresh_auth",
    "JWT_AUTH_REFRESH_COOKIE_PATH": "/",
    "JWT_AUTH_SECURE": True,
    "JWT_AUTH_HTTPONLY": False,
    "JWT_AUTH_SAMESITE": "Lax",
    "JWT_AUTH_RETURN_EXPIRATION": True,
    "JWT_AUTH_COOKIE_USE_CSRF": False,
    "JWT_AUTH_COOKIE_ENFORCE_CSRF_ON_UNAUTHENTICATED": False,
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(days=2),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=30),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
}

GOOGLE_CALLBACK_URL = getenv("GOOGLE_CALLBACK_URL", "http://localhost:8000")
SOCIALACCOUNT_ADAPTER = "authentication.views.social.SocialAccountAdapter"
SOCIALACCOUNT_PROVIDERS = {
    "facebook": {
        "METHOD": "oauth2",
        "SDK_URL": "//connect.facebook.net/{locale}/sdk.js",
        "SCOPE": ["email", "public_profile"],
        "AUTH_PARAMS": {"auth_type": "reauthenticate"},
        "INIT_PARAMS": {"cookie": True},
        "FIELDS": [
            "id",
            "first_name",
            "last_name",
            "middle_name",
            "name",
            "name_format",
            "picture",
            "short_name",
        ],
        "EXCHANGE_TOKEN": True,
        "VERIFIED_EMAIL": False if DEBUG else True,
        "LOCALE_FUNC": lambda request: "en_US",
        "VERSION": "v15.0",
        "GRAPH_API_URL": "https://graph.facebook.com/v15.0/",
    },
    "google": {
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {"access_type": "online"},
    },
}

ACCOUNT_USER_MODEL_USERNAME_FIELD = "username"
ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_EMAIL_NOTIFICATIONS = True
ACCOUNT_USERNAME_MIN_LENGTH = 2
ACCOUNT_USERNAME_MAX_LENGTH = 30
ACCOUNT_USERNAME_REQUIRED = False
ACCOUNT_AUTHENTICATION_METHOD = "username_email"
ACCOUNT_ADAPTER = "user.adapter.UserAccountAdapter"
ACCOUNT_SIGNUP_REDIRECT_URL = NUXT_BASE_URL + "/account"
LOGIN_REDIRECT_URL = NUXT_BASE_URL + "/account"
