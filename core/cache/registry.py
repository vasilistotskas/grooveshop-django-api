from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Iterable

from django.utils.translation import gettext_lazy as _

# ``label`` and ``description`` accept lazy-translated strings via
# ``gettext_lazy``. ty does not narrow ``_StrPromise`` to ``str``, and
# adding a runtime ``str | _StrPromise`` annotation drags Django's
# private ``functional`` types into a public API. Use ``Any`` for these
# two fields to keep the signature ergonomic at call sites.
LazyStr = Any


@dataclass(frozen=True)
class CacheSurface:
    """A named slice of the cache that an admin can purge as a unit.

    A surface owns a set of key patterns across the two backend caches
    (Django's Redis and Nuxt's Nitro Redis) and may declare related
    surfaces that should be purged alongside it (e.g. invalidating
    ``pay_way`` should also invalidate the order serializer cache that
    embeds PayWay payloads).
    """

    code: str
    label: LazyStr
    description: LazyStr
    django_patterns: tuple[str, ...] = ()
    nuxt_patterns: tuple[str, ...] = ()
    related: tuple[str, ...] = ()
    icon: str = "database"
    group: str = "general"
    danger: bool = False


_lock = threading.RLock()
_registry: dict[str, CacheSurface] = {}


def register_surface(surface: CacheSurface) -> CacheSurface:
    """Register a cache surface, replacing any existing entry with the same code."""

    with _lock:
        _registry[surface.code] = surface
    return surface


def get_surface(code: str) -> CacheSurface:
    with _lock:
        try:
            return _registry[code]
        except KeyError as exc:
            raise KeyError(
                _("Unknown cache surface: %(code)s") % {"code": code}
            ) from exc


def iter_surfaces() -> list[CacheSurface]:
    """Return surfaces sorted by group, then label, for stable UI order."""

    with _lock:
        items = list(_registry.values())
    items.sort(key=lambda s: (s.group, str(s.label)))
    return items


def expand_with_related(codes: Iterable[str]) -> list[str]:
    """Expand a list of surface codes by walking ``related`` references.

    Cycles are tolerated; each surface is visited once. Order preserves
    the user's selection so the audit log reads naturally.

    ``danger=True`` surfaces are NEVER included via the related cascade
    — they may only be purged when the operator explicitly selects
    them. This prevents an accidental wipe of a heavyweight cache
    (e.g. parler translations) when an admin clears a small surface
    that happens to reference it.
    """

    seen: set[str] = set()
    ordered: list[str] = []
    queue: list[str] = list(codes)
    is_top_level = True

    with _lock:
        while queue:
            batch = queue
            queue = []
            for code in batch:
                if code in seen or code not in _registry:
                    continue
                surface = _registry[code]
                if not is_top_level and surface.danger:
                    # Block heavy surfaces from auto-cascade; the
                    # operator must opt in by selecting them
                    # directly.
                    continue
                seen.add(code)
                ordered.append(code)
                queue.extend(surface.related)
            is_top_level = False

    return ordered


def _reset_for_tests() -> None:
    """Test-only helper — clear the registry. Not part of the public API."""

    with _lock:
        _registry.clear()
