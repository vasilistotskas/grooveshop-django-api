from typing import override

from allauth.headless.tokens.sessions import (
    SessionTokenStrategy as BaseSessionTokenStrategy,
)
from knox.models import get_token_model


AuthToken = get_token_model()


class SessionTokenStrategy(BaseSessionTokenStrategy):
    @override
    def create_access_token(self, request):
        _, token = AuthToken.objects.create(request.user)
        return token
