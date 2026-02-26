import binascii
import logging
from hmac import compare_digest
from typing import TYPE_CHECKING
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.utils import timezone
from knox.crypto import hash_token
from knox.settings import CONSTANTS, knox_settings

if TYPE_CHECKING:
    from knox.models import AuthToken

logger = logging.getLogger(__name__)

User = get_user_model()


@database_sync_to_async
def authenticate_token(token: str):
    from knox.models import get_token_model  # noqa: PLC0415

    try:
        token = token.strip()
    except AttributeError:
        return AnonymousUser()

    for auth_token in get_token_model().objects.filter(
        token_key=token[: CONSTANTS.TOKEN_KEY_LENGTH]
    ):
        if _cleanup_token(auth_token):
            continue

        try:
            digest = hash_token(token)
        except (TypeError, binascii.Error) as e:
            logger.debug("Error hashing WebSocket token: %s", e)
            continue

        if compare_digest(digest, auth_token.digest):
            if knox_settings.AUTO_REFRESH and auth_token.expiry:
                _renew_token(auth_token)

            return (
                auth_token.user
                if auth_token.user.is_active
                else AnonymousUser()
            )

    return AnonymousUser()


def _renew_token(auth_token: "AuthToken"):
    current_expiry = auth_token.expiry
    new_expiry = timezone.now() + knox_settings.TOKEN_TTL
    auth_token.expiry = new_expiry
    delta = (new_expiry - current_expiry).total_seconds()
    if delta > knox_settings.MIN_REFRESH_INTERVAL:
        auth_token.save(update_fields=("expiry",))


def _cleanup_token(auth_token: "AuthToken"):
    if auth_token.expiry is not None and auth_token.expiry < timezone.now():
        auth_token.delete()
        return True
    return False


class TokenAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        query_string = scope.get("query_string", b"").decode()
        query_params = parse_qs(query_string)

        access_token = query_params.get("access_token", [None])[0]

        if access_token:
            scope["user"] = await authenticate_token(access_token)
        else:
            scope["user"] = AnonymousUser()

        return await super().__call__(scope, receive, send)


def TokenAuthMiddlewareStack(inner):
    return TokenAuthMiddleware(inner)
