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
def get_user(user_id: int):
    try:
        return User.objects.get(id=user_id)
    except User.DoesNotExist:
        return AnonymousUser()


@database_sync_to_async
def authenticate_token(token: str):
    from django.contrib.auth.models import AnonymousUser  # noqa: PLC0415
    from knox.models import get_token_model  # noqa: PLC0415

    logger.debug(f"Authenticating token: {token[:10]}...")

    try:
        token = token.strip()
    except AttributeError:
        logger.debug("Token has no strip method, returning AnonymousUser")
        return AnonymousUser()

    for auth_token in get_token_model().objects.filter(
        token_key=token[: CONSTANTS.TOKEN_KEY_LENGTH]
    ):
        logger.debug(
            f"Found token with key: {token[: CONSTANTS.TOKEN_KEY_LENGTH]}"
        )

        if _cleanup_token(auth_token):
            logger.debug("Token expired and was cleaned up")
            continue

        try:
            digest = hash_token(token)
        except (TypeError, binascii.Error) as e:
            logger.debug(f"Error hashing token: {e!s}")
            continue

        if compare_digest(digest, auth_token.digest):
            logger.debug("Token digest match")
            if knox_settings.AUTO_REFRESH and auth_token.expiry:
                logger.debug("Auto refreshing token")
                _renew_token(auth_token)

            user = (
                auth_token.user
                if auth_token.user.is_active
                else AnonymousUser()
            )
            logger.debug(
                f"Authenticated user: {user.username if not user.is_anonymous else 'AnonymousUser'}"
            )
            return user

        logger.debug("Token digest mismatch")

    logger.debug("No valid token found, returning AnonymousUser")
    return AnonymousUser()


def _renew_token(auth_token: "AuthToken"):
    current_expiry = auth_token.expiry
    new_expiry = timezone.now() + knox_settings.TOKEN_TTL
    auth_token.expiry = new_expiry
    delta = (new_expiry - current_expiry).total_seconds()
    if delta > knox_settings.MIN_REFRESH_INTERVAL:
        auth_token.save(update_fields=("expiry",))
        logger.debug(f"Token renewed, new expiry: {new_expiry}")


def _cleanup_token(auth_token: "AuthToken"):
    if auth_token.expiry is not None and auth_token.expiry < timezone.now():
        auth_token.delete()
        return True
    return False


class TokenAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        logger.debug("TokenAuthMiddleware called")
        query_string = scope.get("query_string", b"").decode()
        query_params = parse_qs(query_string)

        logger.debug(f"Query params: {query_params}")

        session_token = query_params.get("session_token", [None])[0]
        access_token = query_params.get("access_token", [None])[0]
        user_id = query_params.get("user_id", [None])[0]

        logger.debug(
            f"Session token: {session_token[:10] if session_token else None}..."
        )
        logger.debug(
            f"Access token: {access_token[:10] if access_token else None}..."
        )
        logger.debug(f"User ID: {user_id}")

        token = access_token or session_token
        if token:
            logger.debug("TokenAuthMiddleware Token found, authenticating")
            user = await authenticate_token(token)
            scope["user"] = user
            logger.debug(
                f"User authenticated: {user.username if not user.is_anonymous else 'AnonymousUser'}"
            )
        elif user_id:
            logger.debug(f"No token found, but user_id is present: {user_id}")
            user = await get_user(int(user_id))
            scope["user"] = user
            logger.debug(
                f"User retrieved by ID: {user.username if not user.is_anonymous else 'AnonymousUser'}"
            )
        else:
            from django.contrib.auth.models import AnonymousUser  # noqa: PLC0415, I001

            logger.debug("TokenAuthMiddleware Token not found and no user_id")
            scope["user"] = AnonymousUser()

        return await super().__call__(scope, receive, send)


def TokenAuthMiddlewareStack(inner):
    return TokenAuthMiddleware(inner)
