import binascii
from hmac import compare_digest
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from django.utils import timezone
from knox.crypto import hash_token
from knox.settings import CONSTANTS
from knox.settings import knox_settings


@database_sync_to_async
def get_user(user_id):
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.auth import get_user_model

    User = get_user_model()  # noqa

    try:
        return User.objects.get(id=user_id)
    except User.DoesNotExist:
        return AnonymousUser()


class QueryAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        query_string = scope.get("query_string", b"").decode()
        query_params = parse_qs(query_string)
        user_id = query_params.get("user_id", [None])[0]

        if user_id and user_id.isdigit():
            scope["user"] = await get_user(int(user_id))
        else:
            from django.contrib.auth.models import AnonymousUser

            scope["user"] = AnonymousUser()

        return await self.app(scope, receive, send)


def QueryAuthMiddlewareStack(inner):  # noqa
    return QueryAuthMiddleware(inner)


@database_sync_to_async
def authenticate_token(token):
    from django.contrib.auth.models import AnonymousUser
    from knox.models import get_token_model

    try:
        token = token.strip()
    except AttributeError:
        return AnonymousUser()

    for auth_token in get_token_model().objects.filter(token_key=token[: CONSTANTS.TOKEN_KEY_LENGTH]):
        if _cleanup_token(auth_token):
            continue

        try:
            digest = hash_token(token)
        except (TypeError, binascii.Error):
            continue
        if compare_digest(digest, auth_token.digest):
            if knox_settings.AUTO_REFRESH and auth_token.expiry:
                _renew_token(auth_token)
            return auth_token.user if auth_token.user.is_active else AnonymousUser()
    return AnonymousUser()


def _renew_token(auth_token) -> None:
    current_expiry = auth_token.expiry
    new_expiry = timezone.now() + knox_settings.TOKEN_TTL
    auth_token.expiry = new_expiry
    delta = (new_expiry - current_expiry).total_seconds()
    if delta > knox_settings.MIN_REFRESH_INTERVAL:
        auth_token.save(update_fields=("expiry",))


def _cleanup_token(auth_token) -> bool:
    if auth_token.expiry is not None:
        if auth_token.expiry < timezone.now():
            auth_token.delete()
            return True
    return False


class TokenAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        query_string = scope.get("query_string", b"").decode()
        query_params = parse_qs(query_string)

        session_token = query_params.get("session_token", [None])[0]
        access_token = query_params.get("access_token", [None])[0]

        token = access_token or session_token
        if token:
            user = await authenticate_token(token)
            scope["user"] = user
        else:
            from django.contrib.auth.models import AnonymousUser

            scope["user"] = AnonymousUser()

        return await self.app(scope, receive, send)


def tokenAuthMiddlewareStack(inner):
    return TokenAuthMiddleware(inner)
