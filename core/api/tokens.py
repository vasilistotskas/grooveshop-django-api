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

    Knox's AUTO_REFRESH_MAX_TTL setting prevents the *expiry* field from
    being bumped indefinitely, but it does not invalidate tokens that were
    issued before the cap was configured. This subclass adds a hard check
    on ``token.created``: if the token is older than KNOX_ABSOLUTE_MAX_AGE
    it is rejected with 401 regardless of its expiry field.

    Wired into ``REST_FRAMEWORK.DEFAULT_AUTHENTICATION_CLASSES`` in settings
    in place of ``knox.auth.TokenAuthentication``.
    """

    def authenticate_credentials(self, token):
        user, auth_token = super().authenticate_credentials(token)
        age = timezone.now() - auth_token.created
        if age > KNOX_ABSOLUTE_MAX_AGE:
            auth_token.delete()
            raise exceptions.AuthenticationFailed(
                _(
                    "Token has exceeded its maximum lifetime. Please log in again."
                )
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
