"""Purge a cache surface when the data behind it changes.

Every cached surface was purge-on-demand only: ``CacheService.purge``
had exactly two callers, the admin's Cache Management page and the
``clear_cache`` command. Nothing ran on a write, so an operator editing
a row in Django admin saw no change on the storefront until the TTL ran
out — ``DEFAULT_CACHE_TTL`` is 7200s, and Nitro caches the same payload
again on top.

That is not hypothetical. A corrected PayWay description
(``viva_wallet``, 2026-09-10) kept serving the previous English
bank-transfer placeholder to a Greek storefront from two cache tiers at
once, long after the database was right.

Design notes
------------

* **Opt-in per surface** via ``CacheSurface.invalidated_by``. Purging on
  every write is wrong for high-volume models: ``CacheService.purge``
  SCANs Redis and POSTs to the Nuxt purge endpoint, so wiring it to
  ``product.Product`` would fire that per row of a catalogue import.

* **Translations are separate models.** parler stores every language in
  its own row, so a receiver bound only to ``PayWay`` never fires when
  an operator edits the Greek description — which is precisely the edit
  that goes stale. Surfaces declare the translation model explicitly.

* **Coalesced, and after commit.** Saving a model plus three
  translations in one transaction is one logical edit; it purges once,
  and only if the transaction actually commits. Purging inside the
  transaction would let a rolled-back edit evict a still-valid cache and
  re-populate it from uncommitted state.

* **Never fatal.** Cache invalidation failing must not fail the write
  that triggered it. ``CacheService.purge`` already isolates per-surface
  failures and logs them; this module additionally swallows anything
  that escapes, because it runs in an ``on_commit`` callback where an
  exception has no caller to handle it.
"""

from __future__ import annotations

import logging
import threading

from django.apps import apps
from django.db import transaction
from django.db.models.signals import post_delete, post_save

from core.cache.registry import iter_surfaces

logger = logging.getLogger(__name__)

# Surfaces marked stale by the current transaction. Thread-local because
# a transaction belongs to a connection, and a Celery worker or an ASGI
# thread must not coalesce into another's pending set.
_pending = threading.local()


def _pending_codes() -> set[str]:
    codes = getattr(_pending, "codes", None)
    if codes is None:
        codes = set()
        _pending.codes = codes
    return codes


def _flush(schema_name: str) -> None:
    """Purge everything marked stale, once, after commit."""

    codes = _pending_codes()
    if not codes:
        return
    # Clear BEFORE purging: a failure must not leave codes pending for
    # the next transaction on this thread to retry forever.
    ordered = sorted(codes)
    codes.clear()

    # Imported here, not at module scope: ``CacheService`` reaches the
    # cache backend and the Nuxt client at import time, and this module
    # is imported from ``AppConfig.ready()``.
    from core.cache.service import CacheService

    try:
        report = CacheService.purge(ordered)
    except Exception:
        logger.exception(
            "Cache auto-invalidation failed for surfaces=%s schema=%s; "
            "the write succeeded and these surfaces stay stale until "
            "their TTL expires or an operator purges them",
            ordered,
            schema_name,
        )
        return

    failed = report.failed_surfaces
    log = logger.warning if failed else logger.info
    log(
        "Cache auto-invalidated surfaces=%s schema=%s django=%s nuxt=%s "
        "failed=%s",
        ordered,
        schema_name,
        report.django_headline,
        report.nuxt_headline,
        [s.code for s in failed],
    )


def _mark_stale(code: str) -> None:
    from django.db import connection

    _pending_codes().add(code)

    # Schedule on EVERY write rather than only the first. Using "the
    # set is non-empty" as a proxy for "a callback is already queued"
    # was wrong: Django discards on_commit callbacks when a transaction
    # rolls back, but nothing clears this set, so a rolled-back edit
    # left a code pending and made the NEXT transaction believe it was
    # already covered — that commit then purged nothing at all.
    #
    # Queueing one callback per write is cheap because ``_flush``
    # drains the set and returns immediately when it is empty, so only
    # the first callback of a transaction does any work. The residue
    # from a rolled-back transaction is drained by the next commit on
    # this thread, which at worst purges one surface that did not need
    # it — over-purging is safe, under-purging is the bug.
    schema_name = getattr(connection, "schema_name", "-")
    transaction.on_commit(lambda: _flush(schema_name))


def _make_receiver(code: str):
    def receiver(sender, **kwargs):
        # ``raw`` is a loaddata/fixture load. The cache is irrelevant
        # mid-fixture and the surrounding transaction is not a real edit.
        if kwargs.get("raw"):
            return
        _mark_stale(code)

    return receiver


# Keeping strong references is load-bearing: Django's signal registry
# holds receivers weakly, so a closure that nothing else references is
# garbage collected and the signal silently stops firing.
_receivers: list[object] = []


def _dispatch_uid(code: str, label: str) -> str:
    """Stable id so connecting twice is a no-op and disconnect finds it."""

    return f"cache-invalidate:{code}:{label}"


def _iter_declared_models():
    """Yield ``(surface_code, model_label, model)`` for every declared
    model, skipping — loudly — any label that does not resolve."""

    for surface in iter_surfaces():
        for label in surface.invalidated_by:
            # Only the lookup belongs in the ``try``. With the ``yield``
            # inside it, a LookupError raised by the CONSUMER while this
            # generator is suspended would be caught here and reported
            # as an unresolvable model — hiding a real error behind a
            # misleading warning.
            try:
                model = apps.get_model(label)
            except LookupError, ValueError:
                # A surface naming a model that does not exist is a
                # configuration bug, but it must not stop the app from
                # booting — and it must not pass silently either.
                logger.warning(
                    "Cache surface %r declares unknown model %r in "
                    "invalidated_by; that surface will not "
                    "auto-invalidate",
                    surface.code,
                    label,
                )
                continue

            yield surface.code, label, model


def connect_surface_invalidation(*, force: bool = False) -> None:
    """Wire ``post_save``/``post_delete`` for every declared model.

    Idempotent — ``dispatch_uid`` makes a second call a no-op, which
    matters because ``AppConfig.ready()`` can run more than once (the
    autoreloader, and some management commands).

    Skipped when caching is disabled, matching ``core.utils.views
    .cache_methods``: nothing is cached, so there is nothing to
    invalidate, and connecting anyway would make every factory-built
    row in the test suite reach for a Redis client that is not there.
    ``force=True`` is for the tests that exercise this module itself.
    """

    from django.conf import settings

    if getattr(settings, "DISABLE_CACHE", False) and not force:
        return

    for code, label, model in _iter_declared_models():
        receiver = _make_receiver(code)
        _receivers.append(receiver)
        uid = _dispatch_uid(code, label)
        post_save.connect(receiver, sender=model, dispatch_uid=uid, weak=False)
        post_delete.connect(
            receiver, sender=model, dispatch_uid=uid, weak=False
        )


def disconnect_surface_invalidation() -> None:
    """Undo ``connect_surface_invalidation``.

    Exists for the tests that connect explicitly with ``force=True``:
    leaving the receivers attached would make every later PayWay write
    in the same worker reach for a Redis client that tests do not have.
    """

    for code, label, model in _iter_declared_models():
        uid = _dispatch_uid(code, label)
        post_save.disconnect(sender=model, dispatch_uid=uid)
        post_delete.disconnect(sender=model, dispatch_uid=uid)
    _receivers.clear()
