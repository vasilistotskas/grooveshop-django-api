import logging
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache

from notification.views.websocket import build_ticket_cache_key

logger = logging.getLogger(__name__)

User = get_user_model()


@database_sync_to_async
def authenticate_ticket(ticket: str):
    """Consume a single-use WebSocket ticket and return the owning user.

    The ticket is deleted on first read so intercepted values can't be
    replayed — a legitimate client never sends the same ticket twice
    because tickets map 1:1 to connection attempts.

    Uses an atomic Redis GETDEL so that concurrent connection attempts
    using the same ticket value can never both succeed (compare-and-delete
    has a TOCTOU window; GETDEL is a single round-trip).

    Also verifies that at least one live Knox token exists for the user.
    If Knox tokens were revoked (e.g. password/email change) between
    ticket minting and WS connect, the connection is denied even though
    the ticket itself is still valid.
    """
    from knox.models import get_token_model  # noqa: PLC0415

    if not ticket:
        return AnonymousUser()

    raw_key = build_ticket_cache_key(ticket)
    # Resolve the Django-prefixed Redis key that the cache layer stored.
    prefixed_key = cache.make_and_validate_key(raw_key)

    # Atomic GETDEL — returns bytes or None.
    raw_value: bytes | None = cache._cache.get_client(
        prefixed_key, write=True
    ).getdel(prefixed_key)

    if raw_value is None:
        return AnonymousUser()

    # Django's RedisSerializer stores plain ints without pickling them.
    try:
        user_id = int(raw_value)
    except (ValueError, TypeError):
        logger.warning(
            "WS ticket cache value is not a valid user PK: %r", raw_value
        )
        return AnonymousUser()

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return AnonymousUser()

    if not user.is_active:
        return AnonymousUser()

    # Deny connections if all Knox tokens have been revoked — e.g. because
    # the user changed their password between minting the ticket and
    # opening the WebSocket.
    if not get_token_model().objects.filter(user=user).exists():
        logger.info(
            "WS ticket rejected: no live Knox tokens for user %s", user.pk
        )
        return AnonymousUser()

    return user


class TokenAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        query_string = scope.get("query_string", b"").decode()
        query_params = parse_qs(query_string)

        ticket = query_params.get("ticket", [None])[0]

        if ticket:
            scope["user"] = await authenticate_ticket(ticket)
        else:
            scope["user"] = AnonymousUser()

        return await super().__call__(scope, receive, send)


def TokenAuthMiddlewareStack(inner):
    return TokenAuthMiddleware(inner)
