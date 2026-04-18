"""Rosetta storage + DB-backed translation overlay.

`Translation` rows in Postgres are the durable source of truth for
msgstr values. On pod boot and on every cross-pod invalidation tick,
`apply_db_overlay` mutates Django's in-memory gettext catalog so the
process serves DB values without touching disk — the on-disk .po file
is only the msgid schema produced by `makemessages`.

Rosetta edits are mirrored into `Translation` via the
rosetta.signals.entry_changed receiver (see core/signals/rosetta.py).

The version-bumping + reload primitives are unchanged: on save, we
bump a Redis key so other pods' middleware notice and re-apply the
overlay. The file-bytes-in-Redis dance that previously raced the
24 h TTL on deploys has been removed — the DB is the only state
that matters across deploys.
"""

from __future__ import annotations

import logging

from rosetta.storage import CacheRosettaStorage

logger = logging.getLogger(__name__)

TRANSLATION_VERSION_CACHE_KEY = "rosetta:translation_version"


class CacheClearingRosettaStorage(CacheRosettaStorage):
    """Rosetta storage shell; real work happens in signals + overlay."""


def _reload_translations() -> None:
    """Evict cached gettext catalogs so the next lookup rebuilds from disk.

    Django's `trans_real._translations` is the per-language cache, and
    `gettext._translations` is the stdlib cache keyed by absolute .mo
    path that `DjangoTranslation` delegates to. Both must be cleared —
    without the second, a freshly-built DjangoTranslation still merges
    the stale stdlib GNUTranslations and serves pre-save strings until
    the process restarts.
    """
    import gettext

    from django.utils.translation import trans_real

    if hasattr(trans_real, "_translations"):
        trans_real._translations = {}
    if hasattr(trans_real, "_default"):
        trans_real._default = None
    if hasattr(gettext, "_translations"):
        gettext._translations = {}


def _configured_languages() -> list[str]:
    from django.conf import settings

    return [code for code, _name in getattr(settings, "LANGUAGES", [])]


def _overlay_rows(language_code: str) -> list[tuple[str, str, int, str]]:
    """Fetch (msgid, msgid_plural, plural_index, msgstr) tuples from DB.

    Returns an empty list if the Translation table is missing (first
    migrate not yet run), the DB is unreachable, or a test harness
    (e.g. pytest-django) is actively blocking database access.
    """
    try:
        from core.models import Translation
    except Exception as exc:  # pragma: no cover — ImportError during teardown
        logger.debug("Translation model unavailable: %s", exc)
        return []

    try:
        return list(
            Translation.objects.filter(language_code=language_code)
            .exclude(msgstr="")
            .values_list("msgid", "msgid_plural", "plural_index", "msgstr")
        )
    except Exception as exc:
        # Catch a broad Exception rather than the DB-specific ones: in
        # addition to OperationalError / ProgrammingError, pytest-django
        # raises RuntimeError for tests without the django_db mark, and
        # other harnesses may raise their own subclasses.
        logger.debug(
            "Translation overlay rows unavailable for %s: %s",
            language_code,
            exc,
        )
        return []


def apply_db_overlay(language_code: str | None = None) -> None:
    """Overlay DB msgstrs onto Django's in-memory gettext catalog.

    Call with no args to refresh every configured language; call with
    a specific language to refresh a single catalog (e.g. from the
    Rosetta signal handler after one locale's .po was edited).

    Django 6.0 wraps `_catalog` in a `TranslationCatalog` class rather
    than using a plain dict; its `__setitem__` writes into the first
    underlying catalog and preserves the per-catalog plural functions
    (see trans_real.TranslationCatalog). We must mutate through that
    interface rather than replacing `_catalog` with a plain dict —
    downstream `ngettext` calls `self._catalog.plural(...)` which only
    exists on the wrapper.

    Keys by-form:
      - Singular (`msgid_plural` empty): catalog key is the plain msgid str.
      - Plural: catalog key is the tuple `(msgid, plural_index)` — matching
        the format stdlib gettext uses for `ngettext` lookups.

    Safe to call during AppConfig.ready: the function swallows the
    OperationalError / ProgrammingError that fires during the initial
    `migrate` before the Translation table exists, plus any broader
    DB-access errors raised by test harnesses (pytest-django).
    """
    from django.utils.translation import trans_real

    languages = [language_code] if language_code else _configured_languages()

    for lang in languages:
        try:
            catalog = trans_real.translation(lang)
        except Exception as exc:  # pragma: no cover — logging only
            logger.warning("Could not load catalog for %s: %s", lang, exc)
            continue

        try:
            rows = _overlay_rows(lang)
        except Exception as exc:
            logger.debug("Overlay rows unavailable for %s: %s", lang, exc)
            continue

        underlying = getattr(catalog, "_catalog", None)
        if underlying is None:
            continue

        applied = 0
        for msgid, msgid_plural, plural_index, msgstr in rows:
            key = (msgid, plural_index) if msgid_plural else msgid
            # TranslationCatalog.__setitem__ writes to self._catalogs[0];
            # plain-dict _catalog (older Django) also supports subscript
            # assignment. Works for both.
            underlying[key] = msgstr
            applied += 1

        if applied:
            logger.debug(
                "Applied %d DB translations to catalog for %s", applied, lang
            )
