from django.conf import settings
from django.db import connection

from allauth.mfa.adapter import DefaultMFAAdapter

from tenant.credentials import tenant_totp_issuer


def _webauthn_rp_id() -> str:
    """Return the WebAuthn ``rpId`` for the currently-resolved tenant.

    Passkeys are anchored to the registering origin's eTLD+1 — a
    passkey registered on ``store-a.com`` will not assert on
    ``store-b.com``. Returning ``settings.APP_MAIN_HOST_NAME`` for every
    tenant would silently break passkey enrolment on every tenant
    except the platform domain (H2 in MULTI_TENANT_AUDIT.md).

    Resolution order:
      1. ``connection.tenant.primary_domain`` — the public hostname the
         operator entered when creating the tenant.
      2. Domain row matching ``connection.tenant`` via the
         django-tenants DomainMixin (first non-empty value).
      3. ``settings.APP_MAIN_HOST_NAME`` — platform fallback.
      4. ``'localhost'`` — final fallback for unit tests.
    """
    tenant = getattr(connection, "tenant", None)
    if tenant is not None:
        try:
            domain = tenant.domains.filter(is_primary=True).first()
        except Exception:
            domain = None
        if domain and getattr(domain, "domain", ""):
            return domain.domain
    return getattr(settings, "APP_MAIN_HOST_NAME", "localhost")


class MFAAdapter(DefaultMFAAdapter):
    def get_public_key_credential_rp_entity(self):
        name = self._get_site_name()
        return {
            "id": _webauthn_rp_id(),
            "name": name,
        }

    def get_totp_issuer(self) -> str:
        """Return the per-tenant TOTP issuer, falling back to the global
        ``settings.MFA_TOTP_ISSUER`` (or the site name when both are empty).
        """
        issuer = tenant_totp_issuer()
        if issuer:
            return issuer
        # Fall back to the parent implementation which reads
        # app_settings.TOTP_ISSUER (= settings.MFA_TOTP_ISSUER) and
        # falls back further to the site name.
        return super().get_totp_issuer()
