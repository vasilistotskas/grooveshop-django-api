from os import getenv

DEBUG = getenv("DEBUG", "True") == "True"

NUXT_BASE_URL = getenv("NUXT_BASE_URL", "http://localhost:3000")

SOCIALACCOUNT_ADAPTER = "authentication.views.social.SocialAccountAdapter"
SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "VERIFIED_EMAIL": False if DEBUG else True,
        "SCOPE": ["profile", "email", "openid"],
        "AUTH_PARAMS": {"access_type": "online"},
    },
}

ACCOUNT_CHANGE_EMAIL = True
ACCOUNT_MAX_EMAIL_ADDRESSES = 2
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
    "account_reset_password": f"{NUXT_BASE_URL}/account/password/reset",
    "account_reset_password_from_key": f"{NUXT_BASE_URL}/account/password/reset/key/{{key}}",
    "account_signup": f"{NUXT_BASE_URL}/account/registration",
    "socialaccount_login_error": f"{NUXT_BASE_URL}/account/provider/callback",
}
