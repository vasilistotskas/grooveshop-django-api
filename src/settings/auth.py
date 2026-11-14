"""Authentication: backends, passwords, allauth, Knox, MFA and the OIDC provider."""

import datetime
from os import getenv

from settings.env import DEBUG, NUXT_BASE_URL

# allauth's own login page: it serves SHOPPERS (email+password), which
# the OIDC authorize view (/identity/o/authorize) depends on — the
# admin login would reject non-staff. Staff hitting a @login_required
# view land here too and can log in with the same credentials.
LOGIN_URL = "/accounts/login/"
AUTHENTICATION_BACKENDS = [
    "allauth.account.auth_backends.AuthenticationBackend",
    "django.contrib.auth.backends.ModelBackend",
    # Platform-staff-only backend for the admin login (see
    # tenant.auth_backends.PlatformStaffBackend). Its authenticate()
    # always returns None, so it is inert for the storefront's normal
    # login dispatch above — it is listed here ONLY because Django's
    # session-based get_user() requires the backend path recorded in
    # the session to be a member of this setting, or it silently logs
    # the user out on the very next request.
    "tenant.auth_backends.PlatformStaffBackend",
    # Turns a UserTenantMembership role into Django model permissions
    # for the tenant currently on the connection. Answers permissions
    # only (its authenticate() returns None) and grants nothing unless
    # the user object was loaded from the PUBLIC schema by
    # PlatformStaffBackend — so it is confined to admin sessions and
    # cannot be reached by a tenant-schema customer whose pk or email
    # happens to collide with a platform identity.
    "tenant.auth_backends.TenantRolePermissionBackend",
]


AUTH_USER_MODEL = "user.UserAccount"

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# How long the admin "forgot password" link (``admin.password_reset``) works.
# Django's token generator reads it; allauth's reset flow has its own.
PASSWORD_RESET_TIMEOUT = int(getenv("PASSWORD_RESET_TIMEOUT", "3600"))
# Budgets of the admin "forgot password" form: requests per client address
# and per target address inside ``WINDOW_SECONDS``. Past the first it answers
# 429; past the second it answers as if the mail had been sent.
STAFF_PASSWORD_RESET = {
    "PER_IP": int(getenv("STAFF_PASSWORD_RESET_PER_IP", "5")),
    "PER_EMAIL": int(getenv("STAFF_PASSWORD_RESET_PER_EMAIL", "3")),
    "WINDOW_SECONDS": int(
        getenv("STAFF_PASSWORD_RESET_WINDOW_SECONDS", "3600")
    ),
}

# Attempts at a platform-staff sign-in (the Django admin login and the staff
# token endpoint, ``tenant.staff_login_limits``): counted per account and
# proven address, per account, and per proven address inside
# ``WINDOW_SECONDS``; past a budget even the right password is refused. allauth's limiter covers neither.
STAFF_LOGIN_LIMITS = {
    # One account from one proven address: the strict budget.
    "MAX_FAILURES_PER_ACCOUNT_ADDRESS": int(
        getenv("STAFF_LOGIN_MAX_FAILURES_PER_ACCOUNT_ADDRESS", "5")
    ),
    # One account from anywhere: the ceiling for a run spread over many
    # addresses, and the only account limit without a proven address.
    "MAX_FAILURES_PER_ACCOUNT": int(
        getenv("STAFF_LOGIN_MAX_FAILURES_PER_ACCOUNT", "50")
    ),
    "MAX_FAILURES_PER_IP": int(getenv("STAFF_LOGIN_MAX_FAILURES_PER_IP", "20")),
    "WINDOW_SECONDS": int(getenv("STAFF_LOGIN_WINDOW_SECONDS", "900")),
}

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

SOCIALACCOUNT_ADAPTER = "tenant.allauth_adapter.TenantSocialAccountAdapter"
SOCIALACCOUNT_STORE_TOKENS = True
SOCIALACCOUNT_QUERY_EMAIL = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True
SOCIALACCOUNT_EMAIL_VERIFICATION = "mandatory"
SOCIALACCOUNT_REQUESTS_TIMEOUT = 10
# No provider sets VERIFIED_EMAIL: allauth would then mark EVERY address
# the provider returns as verified, overriding the provider's own flag,
# and SOCIALACCOUNT_EMAIL_AUTHENTICATION would sign whoever added a
# customer's address, unverified, to a Discord or GitHub account into
# that customer's account. Each provider's own verified flag decides.
SOCIALACCOUNT_PROVIDERS = {
    "github": {
        "APP": {
            "client_id": getenv("SOCIALACCOUNT_GITHUB_CLIENT_ID", ""),
            "secret": getenv("SOCIALACCOUNT_GITHUB_SECRET", ""),
            "key": "",
        },
        "SCOPE": ["read:user", "user:email"],
    },
    "google": {
        "APP": {
            "client_id": getenv("SOCIALACCOUNT_GOOGLE_CLIENT_ID", ""),
            "secret": getenv("SOCIALACCOUNT_GOOGLE_SECRET", ""),
            "key": "",
        },
        "SCOPE": ["profile", "email", "openid"],
        "AUTH_PARAMS": {"access_type": "offline" if DEBUG else "online"},
    },
    "discord": {
        "APP": {
            "client_id": getenv("SOCIALACCOUNT_DISCORD_CLIENT_ID", ""),
            "secret": getenv("SOCIALACCOUNT_DISCORD_SECRET", ""),
            "key": getenv("SOCIALACCOUNT_DISCORD_PUBLIC_KEY", ""),
        },
        "SCOPE": ["email", "identify"],
    },
    "facebook": {
        "APP": {
            "client_id": getenv("SOCIALACCOUNT_FACEBOOK_CLIENT_ID", ""),
            "secret": getenv("SOCIALACCOUNT_FACEBOOK_SECRET", ""),
        },
        "METHOD": "oauth2",
        "SCOPE": [
            "email",
            "public_profile",
        ],
        "VERSION": "v20.0",
        "GRAPH_API_URL": "https://graph.facebook.com/v20.0",
        "FIELDS": [
            "id",
            "email",
            "first_name",
            "last_name",
            "middle_name",
            "name",
            "name_format",
            "picture",
            "short_name",
        ],
    },
}
SOCIALACCOUNT_FORMS = {
    "disconnect": "allauth.socialaccount.forms.DisconnectForm",
    "signup": "allauth.socialaccount.forms.SignupForm",
}

ACCOUNT_CHANGE_EMAIL = getenv("ACCOUNT_CHANGE_EMAIL", "True") == "True"
ACCOUNT_USER_MODEL_USERNAME_FIELD = "username"
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_EMAIL_NOTIFICATIONS = True
ACCOUNT_USERNAME_MIN_LENGTH = 2
ACCOUNT_USERNAME_MAX_LENGTH = 30
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_ADAPTER = "tenant.allauth_adapter.TenantAccountAdapter"
ACCOUNT_SIGNUP_REDIRECT_URL = NUXT_BASE_URL + "/account"
ACCOUNT_LOGIN_BY_CODE_ENABLED = True
ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED = True
ACCOUNT_EMAIL_VERIFICATION_BY_CODE_TIMEOUT = 300
ACCOUNT_LOGIN_BY_CODE_MAX_ATTEMPTS = 3
ACCOUNT_LOGIN_BY_CODE_TIMEOUT = 300
# A pending code can be re-sent (``POST .../auth/code/resend`` and
# ``.../auth/email/verify/resend``) at most this many times per pending flow,
# and a pending email verification can be pointed at a corrected address
# (``POST .../account/email``) at most ``..._SUPPORTS_CHANGE`` times — the
# typo at signup would otherwise strand the visitor on a code that is going
# to someone else's inbox. Both are allauth's own quotas, not custom views.
ACCOUNT_LOGIN_BY_CODE_SUPPORTS_RESEND = 3
ACCOUNT_EMAIL_VERIFICATION_SUPPORTS_RESEND = 3
ACCOUNT_EMAIL_VERIFICATION_SUPPORTS_CHANGE = 2
# Minimum gap between two verification mails to one address. Under
# verification-by-code allauth answers a resend inside it with a 429 (its
# default is 1/10s/key), so the storefront's "resend in 0:30" timer mirrors
# this number. The login-code resend has no allauth limit of its own and is
# bounded by the per-IP rules in ``core.middleware.allauth_ratelimit``.
ACCOUNT_RESEND_COOLDOWN_SECONDS = 30
ACCOUNT_RATE_LIMITS = {
    "confirm_email": f"1/{ACCOUNT_RESEND_COOLDOWN_SECONDS}s/key",
}
ALLAUTH_USER_CODE_FORMAT = {"numeric": True, "dashed": False, "length": 6}
ACCOUNT_SIGNUP_FORM_HONEYPOT_FIELD = "email_confirm"
ACCOUNT_DEFAULT_HTTP_PROTOCOL = "http" if DEBUG else "https"
ACCOUNT_LOGOUT_ON_PASSWORD_CHANGE = True
ACCOUNT_REAUTHENTICATION_TIMEOUT = int(
    getenv("ACCOUNT_REAUTHENTICATION_TIMEOUT", "300")
)

LOGIN_REDIRECT_URL = NUXT_BASE_URL + "/account"
USERSESSIONS_TRACK_ACTIVITY = True

# Client IP resolution for allauth's session/rate-limit machinery is handled
# by UserAccountAdapter.get_client_ip, which uses core.client_ip's
# client_ip_or_peer (X-Real-IP only with the edge proof). We override
# the adapter instead of setting ALLAUTH_TRUSTED_CLIENT_IP_HEADER because the
# latter hard-requires the header — direct-to-Django paths (health probes,
# Celery-triggered HTTP calls, tests) would otherwise 403 on every hit.

HEADLESS_CLIENTS = ("app",)
HEADLESS_TOKEN_STRATEGY = "core.api.tokens.SessionTokenStrategy"
# ``allauth.core.internal.httpkit.get_frontend_url`` — the function
# password-reset / signup / email-confirmation / social-login-error
# links actually go through — delegates here (via
# ``allauth.headless.adapter.get_adapter()``), NOT to ACCOUNT_ADAPTER.
# TenantHeadlessAdapter rewrites the platform URL below (built from
# NUXT_BASE_URL) to the requesting tenant's own primary domain.
HEADLESS_ADAPTER = "tenant.allauth_adapter.TenantHeadlessAdapter"
HEADLESS_FRONTEND_URLS = {
    "account_confirm_email": f"{NUXT_BASE_URL}/account/verify-email/{{key}}",
    "account_reset_password": f"{NUXT_BASE_URL}/account/password/reset",
    "account_reset_password_from_key": f"{NUXT_BASE_URL}/account/password/reset/key/{{key}}",
    "account_signup": f"{NUXT_BASE_URL}/account/signup",
    "socialaccount_login_error": f"{NUXT_BASE_URL}/account/provider/callback",
}

REST_KNOX = {
    "TOKEN_TTL": datetime.timedelta(days=7),
    "AUTH_HEADER_PREFIX": "Bearer",
    "AUTO_REFRESH": True,
    "MIN_REFRESH_INTERVAL": 86400,  # Only extend TTL if token is >1 day old
    # Caps how far into the future expiry can be pushed on each renewal.
    # A fresh login gets 7 days; a long-lived active session can only
    # push the expiry to (created + 30 days) — after that every renewal
    # is a no-op and the session expires naturally at the 30-day wall.
    "AUTO_REFRESH_MAX_TTL": datetime.timedelta(days=30),
    "TOKEN_LIMIT_PER_USER": 10,  # Prevent unbounded token accumulation per user
}
# Hard reject tokens older than this regardless of their expiry field.
# Mirrors AUTO_REFRESH_MAX_TTL; both should be kept in sync.
# BoundedTokenAuthentication in core/api/tokens.py reads this setting.
KNOX_ABSOLUTE_MAX_AGE = datetime.timedelta(days=30)
KNOX_TOKEN_MODEL = "knox.AuthToken"

# Lifetime of the cart-scoped token the storefront mints for the shopping
# assistant (``POST /api/v1/cart/agent-token``): one pass per conversation.
# The storefront mints a new one when a call is refused.
CART_AGENT_TOKEN_TTL_SECONDS = int(
    getenv("CART_AGENT_TOKEN_TTL_SECONDS", "1800")
)

MFA_ADAPTER = "core.adapter.MFAAdapter"
# No MFA_TOTP_ISSUER: the adapter labels authenticator entries with the
# store's own name (Tenant.totp_issuer, else the store name) — a
# platform-wide issuer would put one name on every store's users.
MFA_RECOVERY_CODE_COUNT = 10
MFA_TOTP_PERIOD = 30
MFA_TOTP_DIGITS = 6
MFA_SUPPORTED_TYPES = ["totp", "recovery_codes", "webauthn"]
MFA_PASSKEY_LOGIN_ENABLED = True
MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN = DEBUG
MFA_PASSKEY_SIGNUP_ENABLED = True
# How long a half-finished admin sign-in (password proven, second factor
# not yet) stays valid in the session (``admin.staff_mfa``). Each step of
# the sign-in restarts the clock.
STAFF_MFA_PENDING_SIGN_IN_SECONDS = int(
    getenv("STAFF_MFA_PENDING_SIGN_IN_SECONDS", "600")
)

# ---------- OIDC identity provider (allauth.idp — agent identity linking) --
# Makes this API an OAuth 2.1 authorization server so AI agents (MCP
# clients) can link a shopper's account: authorization-code + PKCE via
# /identity/o/authorize (allauth's own login page at /accounts/login/
# handles authentication), discovery at /.well-known/openid-configuration.
# Agent-consumable resources live under /api/v1/agent/* and are protected
# by allauth.idp's DRF TokenAuthentication + per-scope permissions.
#
# Dynamic Client Registration (RFC 7591) is open (no initial access
# token) because MCP clients self-register; allauth rate-limits the
# endpoint (3/m/ip) and clients are inert until a user consents.
IDP_OIDC_DCR_ENABLED = getenv("IDP_OIDC_DCR_ENABLED", "True").lower() == "true"
IDP_OIDC_DCR_REQUIRES_INITIAL_ACCESS_TOKEN = False
# RSA/EC private key PEM for signing ID tokens / JWKS. Only required
# once clients request the ``openid`` scope (plain OAuth scopes issue
# opaque access tokens without it); provisioned via sealed secret.
IDP_OIDC_PRIVATE_KEY = getenv("IDP_OIDC_PRIVATE_KEY", "")
IDP_OIDC_ADAPTER = "agent.oidc.AgentOIDCAdapter"
