"""Startup guard: client metadata documents (CIMD) stay off.

``agent.oidc.AgentOIDCAdapter.validate_client_registration`` holds every
client to ``CLIENT_GRANT_TYPES``, but allauth calls it only for dynamic
client registration. A CIMD client takes its grant types from a document
the client hosts and reaches no hook of ours, so enabling CIMD would
bypass the rule.
"""

from __future__ import annotations

from allauth.idp.oidc import app_settings
from django.core.checks import Error, register

CIMD_ENABLED = "agent.E001"


@register()
def client_metadata_documents_stay_off(app_configs, **kwargs):
    if not app_settings.CIMD_ENABLED:
        return []
    return [
        Error(
            "IDP_OIDC_CIMD_ENABLED is on, but a client metadata document "
            "names its own grant types and no registration hook sees them.",
            hint=(
                "Add a grant-type check on the CIMD path, as "
                "AgentOIDCAdapter.validate_client_registration does for "
                "registration, before enabling CIMD."
            ),
            id=CIMD_ENABLED,
        )
    ]
