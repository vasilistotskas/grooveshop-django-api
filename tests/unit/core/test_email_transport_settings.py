"""The SMTP relay in MAILERS is built right from the environment.

Django raises on ``use_tls`` and ``use_ssl`` both set, but only when a
message is first sent, which on this platform means a customer's order
confirmation is the thing that discovers the misconfiguration.
settings.py fails at boot instead. The EMAIL_* environment variables are
the same as before Django 6.1; they now fill MAILERS["relay"]["OPTIONS"].
"""

import importlib

import pytest
from django.core.exceptions import ImproperlyConfigured

SMTP = "django.core.mail.backends.smtp.EmailBackend"


def _reload_settings(monkeypatch, **env):
    env.setdefault("EMAIL_BACKEND", SMTP)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    import settings as settings_module

    return importlib.reload(settings_module)


def _relay_options(module) -> dict:
    return module.MAILERS["relay"]["OPTIONS"]


def test_the_default_mailer_hands_mail_to_the_relay(monkeypatch):
    module = _reload_settings(monkeypatch)

    default = module.MAILERS["default"]
    assert default["BACKEND"] == "core.mail.DemoRecipientSuppressingBackend"
    assert default["OPTIONS"]["relay"] in module.MAILERS


def test_starttls_only_is_accepted(monkeypatch):
    module = _reload_settings(
        monkeypatch, EMAIL_USE_TLS="True", EMAIL_USE_SSL="False"
    )
    assert _relay_options(module)["use_tls"] is True
    assert _relay_options(module)["use_ssl"] is False


def test_implicit_tls_only_is_accepted(monkeypatch):
    """Port 465 relays (Cloudflare Email Service, Resend/SES :465) need
    this, and before EMAIL_USE_SSL existed they simply could not be
    configured."""
    module = _reload_settings(
        monkeypatch, EMAIL_USE_TLS="False", EMAIL_USE_SSL="True"
    )
    assert _relay_options(module)["use_tls"] is False
    assert _relay_options(module)["use_ssl"] is True


def test_both_enabled_is_refused_at_boot(monkeypatch):
    with pytest.raises(ImproperlyConfigured, match="mutually exclusive"):
        _reload_settings(
            monkeypatch, EMAIL_USE_TLS="True", EMAIL_USE_SSL="True"
        )


def test_the_port_is_a_number(monkeypatch):
    module = _reload_settings(monkeypatch, EMAIL_PORT="587")

    assert _relay_options(module)["port"] == 587


def test_email_timeout_defaults_to_a_finite_value(monkeypatch):
    """Django's own default is None — an SMTP socket with no timeout.

    Mail is sent from Celery tasks, so a relay that accepts the TCP
    connection and then goes quiet blocks that worker forever with
    nothing in the logs. Hetzner blocks outbound 465 from both cluster
    nodes and the first send against it hung indefinitely rather than
    erroring; the platform must never inherit the None.
    """
    monkeypatch.delenv("EMAIL_TIMEOUT", raising=False)
    module = _reload_settings(monkeypatch)

    assert isinstance(module.EMAIL_SEND_TIMEOUT, int)
    assert module.EMAIL_SEND_TIMEOUT > 0
    assert _relay_options(module)["timeout"] == module.EMAIL_SEND_TIMEOUT


def test_email_timeout_is_read_from_the_environment(monkeypatch):
    module = _reload_settings(monkeypatch, EMAIL_TIMEOUT="7")

    assert module.EMAIL_SEND_TIMEOUT == 7
    assert _relay_options(module)["timeout"] == 7


def test_a_relay_other_than_smtp_gets_no_connection_options(monkeypatch):
    """The console and locmem backends refuse options they do not know."""
    module = _reload_settings(
        monkeypatch,
        EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
    )

    assert _relay_options(module) == {}
