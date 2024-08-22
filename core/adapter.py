from os import getenv
from typing import Dict
from typing import override

from allauth.mfa.adapter import DefaultMFAAdapter


class MFAAdapter(DefaultMFAAdapter):
    @override
    def get_public_key_credential_rp_entity(self) -> Dict[str, str]:
        name = self._get_site_name()
        return {
            "id": getenv("APP_MAIN_HOST_NAME", "localhost"),
            "name": name,
        }
