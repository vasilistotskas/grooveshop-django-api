import binascii
import logging
from hmac import compare_digest
from typing import TYPE_CHECKING
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.utils import timezone
from knox.crypto import hash_token
from knox.settings import CONSTANTS, knox_settings

from notification.views.websocket import build_ticket_cache_key

if TYPE_CHECKING:
    from knox.models import AuthToken

logger = logging.getLogger(__name__)

User = get_user_model()


@database_sync_to_async
def authenticate_ticket(ticket: str):
    """Consume a single-use WebSocket ticket and return the owning user.

    The ticket is deleted on first read so intercepted values can't be
    replayed — a legitimate client never sends the same ticket twice
    because tickets map 1:1 to connection attempts.
    """
    if not ticket:
        return AnonymousUser()

    key = build_ticket_cache_key(ticket)
    user_id = cache.get(key)
    if user_id is None:
        return AnonymousUser()
    cache.delete(key)

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return AnonymousUser()

    return user if user.is_active else AnonymousUser()


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

        # Prefer single-use ticket (short-lived, can't be replayed).
        # `access_token` remains as a fallback for internal/test callers
        # that can't fetch a ticket; remove once all clients migrate.
        ticket = query_params.get("ticket", [None])[0]
        access_token = query_params.get("access_token", [None])[0]

        if ticket:
            scope["user"] = await authenticate_ticket(ticket)
        elif access_token:
            scope["user"] = await authenticate_token(access_token)
        else:
            scope["user"] = AnonymousUser()

        return await super().__call__(scope, receive, send)


def TokenAuthMiddlewareStack(inner):
    return TokenAuthMiddleware(inner)
