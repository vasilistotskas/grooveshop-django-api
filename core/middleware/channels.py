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
def authenticate_ticket(ticket: str, tenant=None):
    """Consume a single-use WebSocket ticket and return the owning user.

    Runs inside ``tenant_context(tenant)``. ``TenantWebsocketMiddleware``
    resolves the tenant into ``scope`` but deliberately does not touch
    the DB connection, and ``database_sync_to_async`` hands this body to
    asgiref's process-wide executor thread — whose ``connection.tenant``
    is whatever the last request on that thread left behind. Without the
    binding the cache key is built with the wrong schema prefix (the
    ticket was written under the requesting tenant's), so the GETDEL
    misses and a legitimate connection is closed as anonymous; when it
    does hit, the user row is read from whichever schema happened to be
    active, which decides whether the connection joins the tenant's
    staff broadcast group.

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
    from django_tenants.utils import tenant_context
    from knox.models import get_token_model

    if not ticket:
        return AnonymousUser()

    if tenant is None:
        return _authenticate_ticket_inner(ticket, get_token_model())
    with tenant_context(tenant):
        return _authenticate_ticket_inner(ticket, get_token_model())


def _authenticate_ticket_inner(ticket: str, token_model):
    """Ticket consumption proper — always called with the tenant's
    schema already bound by the caller."""
    raw_key = build_ticket_cache_key(ticket)
    # Resolve the Django-prefixed Redis key that the cache layer stored.
    prefixed_key = cache.make_and_validate_key(raw_key)

    # ``cache._cache.get_client`` is Django's built-in RedisCacheClient
    # public API (since 4.0) — calling ``getdel`` on it is the atomic
    # single round-trip we want here. Direct ``cache.get`` + ``cache.
    # delete`` would race under concurrent connect attempts.
    raw_value: bytes | None = cache._cache.get_client(
        prefixed_key, write=True
    ).getdel(prefixed_key)

    if raw_value is None:
        return AnonymousUser()

    # Django's RedisSerializer stores plain ints without pickling them.
    try:
        user_id = int(raw_value)
    except ValueError, TypeError:
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
    if not token_model.objects.filter(user=user).exists():
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

        # Resolve the ticket in the CONNECTING tenant's schema.
        # ``TenantWebsocketMiddleware`` runs outside this one and has
        # already put the tenant in the scope. Cross-tenant replay is
        # structural: the ticket cache key and the knox token table are
        # both schema-scoped, so a ticket minted on one tenant finds
        # nothing on another and the connection stays anonymous.
        tenant = scope.get("tenant")
        if ticket:
            user = await authenticate_ticket(ticket, tenant)
        else:
            user = AnonymousUser()

        scope["user"] = user

        return await super().__call__(scope, receive, send)


def TokenAuthMiddlewareStack(inner):
    return TokenAuthMiddleware(inner)
