from django.conf import settings

from allauth.mfa.adapter import DefaultMFAAdapter

from tenant.credentials import tenant_totp_issuer


class MFAAdapter(DefaultMFAAdapter):
    def get_public_key_credential_rp_entity(self):
        name = self._get_site_name()
        return {
            "id": getattr(settings, "APP_MAIN_HOST_NAME", "localhost"),
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
