"""Outgoing mail: the relay, Resend and the operators' address."""

from os import getenv

from django.core.exceptions import ImproperlyConfigured

from settings.env import PRODUCTION_PROFILE

ADMINS = [email for email in [getenv("ADMIN_EMAIL", "")] if email]

# Outgoing mail is configured in MAILERS (below), built from the same
# EMAIL_* environment variables as before. Django 6.1 deprecates the
# EMAIL_* settings and refuses them beside MAILERS, so the values live in
# these lowercase names, which Django does not read as settings.
#
# The backend that actually delivers: console, locmem or SMTP.
_email_relay_backend = getenv(
    "EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend"
)
_email_host_password = getenv("EMAIL_HOST_PASSWORD", "changeme")
# Which transport carries the pipeline's mail (``emails.transport``): the
# SMTP relay above, or Resend's HTTP API. Production stays on SMTP until the
# platform's Resend team exists and one live send is verified.
EMAIL_TRANSPORT = getenv("EMAIL_TRANSPORT", "smtp")
if EMAIL_TRANSPORT not in ("smtp", "resend"):
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        f"EMAIL_TRANSPORT must be 'smtp' or 'resend', not {EMAIL_TRANSPORT!r}."
    )
# The platform's Resend team: carries the platform's own mail and every store
# without its own verified domain. A store with its own domain has its key on
# its Tenant row and never falls back to this one.
RESEND_API_KEY = getenv("RESEND_API_KEY", "")
# The signing secret of the webhook endpoint of that same team, registered on
# the platform host (``emails.webhooks``). A store with its own team keeps
# its secret on its Tenant row. Empty: the platform host answers 503.
RESEND_WEBHOOK_SECRET = getenv("RESEND_WEBHOOK_SECRET", "")
# How many times a staff member may use the admin's Send test per hour.
EMAIL_TEST_SENDS_PER_HOUR = int(getenv("EMAIL_TEST_SENDS_PER_HOUR", "10"))
RESEND_WEBHOOK_MAX_BODY_BYTES = int(
    getenv("RESEND_WEBHOOK_MAX_BODY_BYTES", str(256 * 1024))
)
if PRODUCTION_PROFILE:
    from django.core.exceptions import ImproperlyConfigured

    if EMAIL_TRANSPORT == "smtp" and _email_host_password == "changeme":
        raise ImproperlyConfigured(
            "EMAIL_HOST_PASSWORD must be set in production "
            "(current value is the insecure default 'changeme')."
        )
    if EMAIL_TRANSPORT == "resend" and not RESEND_API_KEY:
        raise ImproperlyConfigured(
            "RESEND_API_KEY must be set in production when "
            "EMAIL_TRANSPORT is 'resend'."
        )
# STARTTLS (upgrade a plaintext connection, conventionally port 587)
# versus implicit TLS (the socket is encrypted from the first byte,
# conventionally port 465). Django treats these as mutually exclusive and
# raises ImproperlyConfigured if both are set, so exposing only
# EMAIL_USE_TLS silently made every implicit-TLS relay unreachable —
# Cloudflare Email Service (smtp.mx.cloudflare.net:465) and the :465
# endpoints of Resend, Postmark and SES among them. Defaulting SSL to off
# keeps the existing STARTTLS behaviour unchanged.
_email_use_tls = getenv("EMAIL_USE_TLS", "True") == "True"
_email_use_ssl = getenv("EMAIL_USE_SSL", "False") == "True"
if _email_use_tls and _email_use_ssl:
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        "EMAIL_USE_TLS and EMAIL_USE_SSL are mutually exclusive — set "
        "exactly one. Use EMAIL_USE_TLS for STARTTLS (port 587) and "
        "EMAIL_USE_SSL for implicit TLS (port 465). Django would "
        "otherwise fail later, at the first send, rather than at boot."
    )
# Django defaults EMAIL_TIMEOUT to None, which leaves the SMTP socket with
# no timeout at all: against a relay that accepts the TCP connection and
# then goes quiet — or a port silently dropped by the host's firewall — a
# send blocks forever and pins the Celery worker that ran it. Mail is sent
# from tasks, so the failure mode is a worker pool that drains to zero with
# nothing in the logs. This is not hypothetical: Hetzner blocks outbound 465
# from both nodes, and the first send against it hung indefinitely rather
# than erroring. The value is a per-operation socket timeout, not a deadline
# for the whole message, so it stays comfortable for large invoice-PDF
# attachments. Resend's HTTP API (``emails.resend``) uses the same value.
EMAIL_SEND_TIMEOUT = int(getenv("EMAIL_TIMEOUT", "20"))
_SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
MAILERS = {
    # Every sender goes through this one, so it is the one place a store's
    # SHARED demo login is kept out of the recipient list
    # (``core.mail``). A passthrough everywhere else: the suppression list
    # is empty unless DEMO_ACCOUNT_ENABLED is on for that schema.
    "default": {
        "BACKEND": "core.mail.DemoRecipientSuppressingBackend",
        "OPTIONS": {"relay": "relay"},
    },
    # What "default" hands the mail to. Only the SMTP backend takes
    # connection options; console and locmem refuse unknown ones.
    "relay": {
        "BACKEND": _email_relay_backend,
        "OPTIONS": (
            {
                "host": getenv("EMAIL_HOST", "localhost"),
                "port": int(getenv("EMAIL_PORT", "25")),
                "username": getenv("EMAIL_HOST_USER", "localhost@gmail.com"),
                "password": _email_host_password,
                "use_tls": _email_use_tls,
                "use_ssl": _email_use_ssl,
                "timeout": EMAIL_SEND_TIMEOUT,
            }
            if _email_relay_backend == _SMTP_BACKEND
            else {}
        ),
    },
}
DEFAULT_FROM_EMAIL = getenv("DEFAULT_FROM_EMAIL", "localhost@gmail.com")
INFO_EMAIL = getenv("INFO_EMAIL", "localhost@gmail.com")
# The platform operators' address is the ADMINS setting: the ADMIN_EMAIL env
# var is read once at module import time into it (see above), and the
# ``platform.*`` operator alerts (``emails.recipients.platform_operators``)
# go there from ``DEFAULT_FROM_EMAIL``.
