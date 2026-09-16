"""The SMTP transport settings must reject an impossible combination.

Django raises ImproperlyConfigured if both EMAIL_USE_TLS and
EMAIL_USE_SSL are set, but only when a message is first sent — which on
this platform means a customer's order confirmation is the thing that
discovers the misconfiguration. settings.py fails at boot instead.
"""

import importlib

import pytest
from django.core.exceptions import ImproperlyConfigured


def _reload_settings(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    import settings as settings_module

    return importlib.reload(settings_module)


def test_starttls_only_is_accepted(monkeypatch):
    module = _reload_settings(
        monkeypatch, EMAIL_USE_TLS="True", EMAIL_USE_SSL="False"
    )
    assert module.EMAIL_USE_TLS is True
    assert module.EMAIL_USE_SSL is False


def test_implicit_tls_only_is_accepted(monkeypatch):
    """Port 465 relays (Cloudflare Email Service, Resend/SES :465) need
    this, and before EMAIL_USE_SSL existed they simply could not be
    configured."""
    module = _reload_settings(
        monkeypatch, EMAIL_USE_TLS="False", EMAIL_USE_SSL="True"
    )
    assert module.EMAIL_USE_TLS is False
    assert module.EMAIL_USE_SSL is True


def test_both_enabled_is_refused_at_boot(monkeypatch):
    with pytest.raises(ImproperlyConfigured, match="mutually exclusive"):
        _reload_settings(
            monkeypatch, EMAIL_USE_TLS="True", EMAIL_USE_SSL="True"
        )


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

    assert module.EMAIL_TIMEOUT is not None
    assert isinstance(module.EMAIL_TIMEOUT, int)
    assert module.EMAIL_TIMEOUT > 0


def test_email_timeout_is_read_from_the_environment(monkeypatch):
    module = _reload_settings(monkeypatch, EMAIL_TIMEOUT="7")

    assert module.EMAIL_TIMEOUT == 7
