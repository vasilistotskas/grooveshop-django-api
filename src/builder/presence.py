"""Who else has a document open in the editor.

A soft signal, not a lock: the editor sends a heartbeat naming the documents
it has open and gets back who else sent one for each of them recently, so it
can show "Maria is editing this page" before two people overwrite each other
(the draft's ``If-Match`` revision still decides, whatever this says).

Everything lives in the cache, under the active schema (the cache key
function puts the store first, so a heartbeat on one store is invisible on
another), and expires on its own:

- ``builder:presence:who:<document>:<staff id>``: ``{name, since}``, for
  ``BUILDER_PRESENCE_TTL_SECONDS``; the one record that says a person is
  here. ``since`` survives a heartbeat and starts again after a lapse.
- ``builder:presence:on:<document>``: the Redis set of staff ids to look at,
  so listing a document never scans the keyspace. A member whose record has
  expired is dropped the next time the document is read.
- ``builder:presence:open:<staff id>``: the documents that person's last
  heartbeat named, so the next heartbeat (or a sign-out) can let go of the
  ones no longer open.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django.utils.translation import gettext as _

from builder.errors import DocumentInvalid, Issue
from builder.models import kind_of_key


def _who_key(document: str, staff: int | str) -> str:
    return f"builder:presence:who:{document}:{staff}"


def _on_key(document: str) -> str:
    return f"builder:presence:on:{document}"


def _open_key(staff: int) -> str:
    return f"builder:presence:open:{staff}"


def _validated(documents: list[str]) -> list[str]:
    unique = list(dict.fromkeys(documents))
    problems = [
        Issue(
            path=f"/documents/{index}",
            code="invalid_key",
            message=_("%(key)s is not a document key.") % {"key": key},
        )
        for index, key in enumerate(unique)
        if kind_of_key(key) is None
    ]
    if len(unique) > settings.BUILDER_PRESENCE_MAX_DOCUMENTS:
        problems.append(
            Issue(
                path="/documents",
                code="too_many_documents",
                message=_("A heartbeat can name at most %(limit)s documents.")
                % {"limit": settings.BUILDER_PRESENCE_MAX_DOCUMENTS},
            )
        )
    if problems:
        raise DocumentInvalid(problems)
    return unique


def _others(document: str, me: int) -> list[dict[str, Any]]:
    """Who else is on *document*: ``[{id, name, since}]``, oldest first.
    Members whose record has lapsed are dropped from the set here."""
    ids = cache.set_members(_on_key(document)) - {str(me)}
    if not ids:
        return []
    records = cache.get_many([_who_key(document, staff) for staff in ids])
    present = []
    lapsed = []
    for staff in ids:
        record = records.get(_who_key(document, staff))
        if record is None:
            lapsed.append(staff)
        else:
            present.append({"id": int(staff), **record})
    cache.remove_from_set(_on_key(document), lapsed)
    return sorted(present, key=lambda entry: (entry["since"], entry["id"]))


def _leave(staff: int, documents: list[str]) -> None:
    for key in documents:
        cache.delete(_who_key(key, staff))
        cache.remove_from_set(_on_key(key), [str(staff)])
    cache.remove_from_set(_open_key(staff), documents)


def heartbeat(
    *, staff: int, name: str, documents: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """Record that *staff* has *documents* open (and only these) and answer
    who else has each: ``{document: [{id, name, since}]}``, the caller left
    out."""
    keys = _validated(documents)
    ttl = settings.BUILDER_PRESENCE_TTL_SECONDS
    now = timezone.now().isoformat()
    _leave(staff, sorted(cache.set_members(_open_key(staff)) - set(keys)))

    held = cache.get_many([_who_key(key, staff) for key in keys])
    answer: dict[str, list[dict[str, Any]]] = {}
    for key in keys:
        record = held.get(_who_key(key, staff))
        cache.set(
            _who_key(key, staff),
            {"name": name, "since": record["since"] if record else now},
            ttl,
        )
        cache.add_to_set(_on_key(key), [str(staff)], ttl * 2)
        answer[key] = _others(key, staff)
    if keys:
        cache.add_to_set(_open_key(staff), keys, ttl * 2)
    return answer


def leave(staff: int) -> None:
    """*staff* closed the editor: they are no longer on any document."""
    _leave(staff, sorted(cache.set_members(_open_key(staff))))
