from allauth.mfa.adapter import DefaultMFAAdapter
from django.conf import settings
from django.db import connection

from tenant.credentials import tenant_site_name, tenant_totp_issuer


def _webauthn_rp_id() -> str:
    """Return the WebAuthn ``rpId`` for the currently-resolved tenant.

    Passkeys are anchored to the registering origin's eTLD+1 — a
    passkey registered on ``store-a.com`` will not assert on
    ``store-b.com``. Returning ``settings.APP_MAIN_HOST_NAME`` for every
    tenant would silently break passkey enrolment on every tenant
    except the platform domain.

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
    """Tenant-scoped MFA identity.

    Both the passkey relying-party name and the TOTP issuer are the
    STORE the user enrolled on. allauth's defaults read the Site
    framework's current site for both, which on a multi-tenant
    deployment is whichever ``Site`` row ``SITE_ID`` points at — one
    store's name shown to every other store's users.
    """

    def get_public_key_credential_rp_entity(self):
        return {
            "id": _webauthn_rp_id(),
            "name": tenant_site_name(),
        }

    def get_totp_issuer(self) -> str:
        return tenant_totp_issuer()
