"""The session a request really has, for analytics rows."""

from __future__ import annotations

from typing import Any


def persisted_session_key(request: Any) -> str:
    """The request's session key when that session is stored, else ``""``.

    Until a session is loaded, Django's ``SessionBase.session_key`` is
    the raw ``sessionid`` cookie, checked for nothing but a length of at
    least 8. An anonymous search never loads its session, so whatever a
    client sent reached the analytics as if it were a session: on
    2026-10-13 a 227-character token made 20 ``save_search_query`` runs
    fail on ``SearchQuery.session_key``'s 40 characters, and the
    recommendation events cut such values to 40 and stored them anyway.

    ``exists`` costs one cache lookup on the ``cached_db`` backend (the
    database only on a miss), and only when a cookie is present at all.
    """
    session = getattr(request, "session", None)
    key = getattr(session, "session_key", None)
    if not key or session is None:
        return ""
    return key if session.exists(key) else ""
