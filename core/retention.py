"""One batched delete for every retention sweep.

A sweep that deletes years of backlog in one statement holds a long lock
and loads every row into the deletion collector; deleting by primary-key
batch bounds both. The batch size is ``settings.RETENTION_PURGE_BATCH_SIZE``.
"""

from __future__ import annotations

from django.conf import settings
from django.db.models import QuerySet


def delete_in_batches(stale: QuerySet) -> int:
    """Delete every row of ``stale``, one batch of primary keys at a time.

    Goes through the ORM, never a raw statement, so cascades and the
    signals that remove a row's files still run. Returns the number of
    ``stale``'s own model rows deleted, not the cascaded ones.
    """
    model = stale.model
    batch_size = settings.RETENTION_PURGE_BATCH_SIZE
    deleted = 0
    while pks := list(stale.values_list("pk", flat=True)[:batch_size]):
        _, per_model = model._base_manager.filter(pk__in=pks).delete()
        deleted += per_model.get(model._meta.label, 0)
    return deleted


def purge_orphaned_user_sessions() -> int:
    """Delete allauth ``UserSession`` rows whose Django session is gone.

    allauth purges a user's dead rows only when that same user logs in
    again or opens the sessions page (``UserSessionManager.purge_and_list``),
    so a customer who never returns keeps an IP and a user agent forever.
    Run it right after ``clearsessions``: the session row is the source of
    truth, so a row without one is, by allauth's own rule, dead. Needs no
    age setting.
    """
    from allauth.usersessions.models import UserSession
    from django.contrib.sessions.models import Session

    return delete_in_batches(
        UserSession.objects.exclude(
            session_key__in=Session.objects.values("session_key")
        )
    )


def purge_expired_auth_tokens() -> int:
    """Delete knox tokens past their expiry, in the active schema.

    knox deletes a user's expired tokens only when that user authenticates
    again, and ships no sweep. The token's own ``expiry`` is the rule, so
    there is no setting. Knox lives in the store schemas only: a schema
    without its table (public) has none to purge.
    """
    from django.db import connection
    from django.utils import timezone
    from knox.models import get_token_model

    token_model = get_token_model()
    if token_model._meta.db_table not in connection.introspection.table_names():
        return 0
    return delete_in_batches(
        token_model.objects.filter(expiry__lt=timezone.now())
    )
