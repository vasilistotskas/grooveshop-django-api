from __future__ import annotations

from django.db import connection

from user.adapter import UserAccountAdapter


class TenantAccountAdapter(UserAccountAdapter):
    """Dynamic frontend URLs per tenant for email links."""

    def _get_tenant_domain(self):
        tenant = getattr(connection, "tenant", None)
        if tenant is None:
            return None
        domain = tenant.domains.filter(is_primary=True).first()
        return domain.domain if domain else None

    def get_email_confirmation_url(self, request, emailconfirmation):
        domain = self._get_tenant_domain()
        if domain:
            return (
                f"https://{domain}/account/verify-email/{emailconfirmation.key}"
            )
        return super().get_email_confirmation_url(request, emailconfirmation)

    def get_reset_password_url(self, request):
        domain = self._get_tenant_domain()
        if domain:
            return f"https://{domain}/account/password/reset"
        return super().get_reset_password_url(request)
