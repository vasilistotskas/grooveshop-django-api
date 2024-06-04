from os import getenv

DEBUG = getenv("DEBUG", "True") == "True"

NUXT_BASE_URL = getenv("NUXT_BASE_URL", "http://localhost:3000")

SOCIALACCOUNT_ADAPTER = "user.adapter.SocialAccountAdapter"
SOCIALACCOUNT_STORE_TOKENS = True
SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "APP": {
            "client_id": getenv("SOCIALACCOUNT_GOOGLE_CLIENT_ID", ""),
            "secret": getenv("SOCIALACCOUNT_GOOGLE_SECRET", ""),
            "key": "",
        },
        "SCOPE": ["profile", "email", "openid"],
        "AUTH_PARAMS": {"access_type": "online"},
    },
}
SOCIALACCOUNT_FORMS = {
    "disconnect": "allauth.socialaccount.forms.DisconnectForm",
    "signup": "allauth.socialaccount.forms.SignupForm",
}

ACCOUNT_CHANGE_EMAIL = True if DEBUG else False
ACCOUNT_MAX_EMAIL_ADDRESSES = 2 if DEBUG else None
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
ACCOUNT_LOGIN_BY_CODE_ENABLED = True
ACCOUNT_LOGIN_BY_CODE_MAX_ATTEMPTS = 3
ACCOUNT_LOGIN_BY_CODE_TIMEOUT = 300
ACCOUNT_SIGNUP_FORM_HONEYPOT_FIELD = "email_confirm"

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
