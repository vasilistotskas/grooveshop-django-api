from os import getenv

DEBUG = getenv("DEBUG", "True") == "True"

NUXT_BASE_URL = getenv("NUXT_BASE_URL", "http://localhost:3000")

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
USERSESSIONS_TRACK_ACTIVITY = True

HEADLESS_ONLY = True
HEADLESS_TOKEN_STRATEGY = "core.api.tokens.SessionTokenStrategy"
HEADLESS_FRONTEND_URLS = {
    "account_confirm_email": f"{NUXT_BASE_URL}/account/verify-email/{{key}}",
    "account_reset_password": "https://app.project.org/account/password/reset",
    "account_reset_password_from_key": f"{NUXT_BASE_URL}/account/password/reset/key/{{key}}",
    "account_signup": f"{NUXT_BASE_URL}/account/registration",
    "socialaccount_login_error": f"{NUXT_BASE_URL}/account/provider/callback",
}
