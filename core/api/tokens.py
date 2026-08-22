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
    """Knox TokenAuthentication with an absolute per-token lifetime cap.

    Rejects tokens older than ``KNOX_ABSOLUTE_MAX_AGE`` regardless of
    their ``expiry`` field (see the module docstring) — a rolling
    ``AUTO_REFRESH`` window otherwise lets a single token live forever.

    Cross-tenant replay needs no check here: knox sits in TENANT_APPS
    only, so ``knox_authtoken`` is a per-schema table and a token minted
    on one tenant does not exist in another's. That is structural, not
    defence in depth.

    Wired into ``REST_FRAMEWORK.DEFAULT_AUTHENTICATION_CLASSES`` in settings
    in place of ``knox.auth.TokenAuthentication``.
    """

    @staticmethod
    def enforce_absolute_age(auth_token) -> None:
        """Reject (and delete) a token older than the absolute cap.

        A method of its own so
        ``tenant.api_tokens.PlatformStaffTokenAuthentication`` — which
        cannot call ``authenticate_credentials`` here because knox's
        implementation hardwires ``get_token_model()`` — applies the
        SAME cap to staff tokens instead of quietly diverging.
        """
        age = timezone.now() - auth_token.created
        if age > KNOX_ABSOLUTE_MAX_AGE:
            auth_token.delete()
            raise exceptions.AuthenticationFailed(
                _(
                    "Token has exceeded its maximum lifetime. "
                    "Please log in again."
                )
            )

    def authenticate_credentials(self, token):
        user, auth_token = super().authenticate_credentials(token)

        # 1. Absolute age cap.
        self.enforce_absolute_age(auth_token)

        # 2. Tenant binding needs no check here: knox is in TENANT_APPS
        # only, so ``knox_authtoken`` is a per-schema table and a token
        # minted on tenant A literally does not exist in tenant B's
        # table — ``authenticate_credentials`` above fails to find it and
        # raises before reaching this point. Verified on staging: a
        # webside token returns 401 on another tenant's API host.
        #
        # This used to additionally require a ``UserTenantMembership``.
        # That table lives in the public schema with an FK to
        # ``public.user_useraccount``, while shoppers are created in
        # their tenant's schema — so the check rejected every ordinary
        # customer, and the grant that tried to satisfy it made signup
        # 500. Membership is now a staff-only concept (see
        # ``admin.admin.MyAdminSite.has_permission``), and gating
        # customer API access on it was never what isolated them.
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
