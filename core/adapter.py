from django.conf import settings

from allauth.mfa.adapter import DefaultMFAAdapter


class MFAAdapter(DefaultMFAAdapter):
    def get_public_key_credential_rp_entity(self):
        name = self._get_site_name()
        return {
            "id": getattr(settings, "APP_MAIN_HOST_NAME", "localhost"),
            "name": name,
        }
