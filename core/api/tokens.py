from datetime import timedelta

from allauth.headless.tokens.strategies.sessions import (
    SessionTokenStrategy as BaseSessionTokenStrategy,
)
from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from knox.auth import TokenAuthentication as KnoxTokenAuthentication
from knox.models import get_token_model
from knox.settings import knox_settings
from rest_framework import exceptions

AuthToken = get_token_model()

# Hard lifetime cap for Knox tokens when AUTO_REFRESH is enabled.
# AUTO_REFRESH_MAX_TTL (set in REST_KNOX) caps *renewal* — the expiry
# field is never pushed beyond created + MAX_TTL. But an already-issued
# token whose expiry was set before the cap was introduced could still
# have a far-future expiry. This additional check in authenticate_credentials
# rejects any token whose *creation* timestamp is older than
# KNOX_ABSOLUTE_MAX_AGE regardless of its expiry field, closing that gap.
#
# Value: 30 days — long enough that a user who opens the app daily never
# sees an unexpected logout, short enough to bound the blast radius if a
# token is compromised without the user noticing.
KNOX_ABSOLUTE_MAX_AGE: timedelta = getattr(
    settings, "KNOX_ABSOLUTE_MAX_AGE", timedelta(days=30)
)


class BoundedTokenAuthentication(KnoxTokenAuthentication):
    """Knox TokenAuthentication with an absolute per-token lifetime cap and
    tenant-binding check.

    Two defences layered on top of stock Knox authentication:

    1. **Absolute age cap** — rejects tokens older than KNOX_ABSOLUTE_MAX_AGE
       regardless of their ``expiry`` field (see module-level docstring).

    2. **Tenant binding** — after Knox validates the token, verifies that the
       authenticated user has an active ``UserTenantMembership`` for the
       current ``connection.tenant``.  Knox already isolates token tables per
       schema (TENANT_APPS placement), but this defence-in-depth check at the
       authentication layer ensures that even if a future code-path changes
       the Knox configuration, a token from tenant-A is still rejected on
       tenant-B's domain before it reaches any permission class.

       The check is skipped when the connection is in the public schema
       (admin paths, health probes) so platform-level tooling keeps working.

    Wired into ``REST_FRAMEWORK.DEFAULT_AUTHENTICATION_CLASSES`` in settings
    in place of ``knox.auth.TokenAuthentication``.
    """

    def authenticate_credentials(self, token):
        user, auth_token = super().authenticate_credentials(token)

        # 1. Absolute age cap.
        age = timezone.now() - auth_token.created
        if age > KNOX_ABSOLUTE_MAX_AGE:
            auth_token.delete()
            raise exceptions.AuthenticationFailed(
                _(
                    "Token has exceeded its maximum lifetime. "
                    "Please log in again."
                )
            )

        # 2. Tenant binding — only enforced when a non-public tenant is active.

        from tenant.membership import (  # noqa: PLC0415
            get_current_tenant,
            user_has_tenant_access,
        )

        tenant = get_current_tenant()
        if tenant is not None and not user_has_tenant_access(user, tenant):
            raise exceptions.PermissionDenied(
                _("You do not have access to this store.")
            )

        return user, auth_token


class SessionTokenStrategy(BaseSessionTokenStrategy):
    def create_access_token(self, request):
        user = request.user
        limit = knox_settings.TOKEN_LIMIT_PER_USER
        if limit is not None:
            qs = AuthToken.objects.filter(user=user).order_by("created")
            excess = qs.count() - (limit - 1)  # leave room for the new token
            if excess > 0:
                pks = list(qs.values_list("pk", flat=True)[:excess])
                AuthToken.objects.filter(pk__in=pks).delete()
        _, token = AuthToken.objects.create(user)
        return token
