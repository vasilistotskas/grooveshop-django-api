from allauth.headless.tokens.strategies.sessions import (
    SessionTokenStrategy as BaseSessionTokenStrategy,
)
from knox.models import get_token_model
from knox.settings import knox_settings

AuthToken = get_token_model()


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
