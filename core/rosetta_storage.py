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
from typing import Iterable

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


def _overlay_rows(language_code: str) -> Iterable[tuple[str, str, int, str]]:
    """Fetch (msgid, msgid_plural, plural_index, msgstr) tuples from DB.

    Returns an empty iterable if the Translation table is missing
    (first migrate not yet run) or the DB is unreachable.
    """
    from django.db.utils import OperationalError, ProgrammingError

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
    except (OperationalError, ProgrammingError) as exc:
        logger.debug(
            "Translation table not available for overlay (%s): %s",
            language_code,
            exc,
        )
        return []


def apply_db_overlay(language_code: str | None = None) -> None:
    """Overlay DB msgstrs onto Django's in-memory gettext catalog.

    Call with no args to refresh every configured language; call with
    a specific language to refresh a single catalog (e.g. from the
    Rosetta signal handler after one locale's .po was edited).

    Keys by-form:
      - Singular (`msgid_plural` empty): catalog key is the plain msgid str.
      - Plural: catalog key is the tuple `(msgid, plural_index)` — matching
        the format stdlib gettext uses for `ngettext` lookups.

    Safe to call during AppConfig.ready: the function swallows the
    OperationalError / ProgrammingError that fires during the initial
    `migrate` before the Translation table exists.
    """
    from django.utils.translation import trans_real

    languages = [language_code] if language_code else _configured_languages()

    for lang in languages:
        try:
            catalog = trans_real.translation(lang)
        except Exception as exc:  # pragma: no cover — logging only
            logger.warning("Could not load catalog for %s: %s", lang, exc)
            continue

        base = dict(getattr(catalog, "_catalog", {}) or {})
        applied = 0
        for msgid, msgid_plural, plural_index, msgstr in _overlay_rows(lang):
            if msgid_plural:
                base[(msgid, plural_index)] = msgstr
            else:
                base[msgid] = msgstr
            applied += 1

        # Atomic rebind under GIL — no lock needed for single-attribute assignment.
        catalog._catalog = base
        if applied:
            logger.debug(
                "Applied %d DB translations to catalog for %s", applied, lang
            )
