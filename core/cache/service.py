from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable

from core.cache import nuxt as nuxt_client
from core.cache.protected import filter_protected
from core.cache.registry import (
    CacheSurface,
    expand_with_related,
    get_surface,
    iter_surfaces,
)
from core.caches import cache_instance

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser

logger = logging.getLogger(__name__)


@dataclass
class SurfaceResult:
    code: str
    django_matched: int = 0
    django_deleted: int = 0
    django_blocked: int = 0
    nuxt_matched: int = 0
    nuxt_deleted: int = 0
    nuxt_blocked: int = 0
    nuxt_error: str | None = None

    @property
    def total_deleted(self) -> int:
        return self.django_deleted + self.nuxt_deleted


@dataclass
class PurgeReport:
    surfaces: list[SurfaceResult] = field(default_factory=list)
    dry_run: bool = False

    @property
    def total_django(self) -> int:
        return sum(s.django_deleted for s in self.surfaces)

    @property
    def total_nuxt(self) -> int:
        return sum(s.nuxt_deleted for s in self.surfaces)

    @property
    def total_deleted(self) -> int:
        return self.total_django + self.total_nuxt

    @property
    def total_blocked(self) -> int:
        return sum(s.django_blocked + s.nuxt_blocked for s in self.surfaces)


class CacheService:
    """Top-level cache management API used by admin views, management
    commands, signals, and tests.

    The service layers two responsibilities: it asks each ``CacheSurface``
    for its key patterns, then delegates to the Django cache backend or
    the Nuxt purge endpoint to run the actual UNLINK. Protected keys
    (throttle counters, sessions, queues) are filtered out *after*
    SCAN so a misregistered surface can never wipe security state.
    """

    @staticmethod
    def list_surfaces() -> list[CacheSurface]:
        return iter_surfaces()

    @staticmethod
    def count(codes: Iterable[str]) -> dict[str, int]:
        result: dict[str, int] = {}
        for code in codes:
            try:
                surface = get_surface(code)
            except KeyError:
                continue
            count = 0
            for pattern in surface.django_patterns:
                count += len(cache_instance.keys(pattern))
            result[code] = count
        return result

    @staticmethod
    def purge(
        codes: Iterable[str],
        *,
        dry_run: bool = False,
        actor: "AbstractBaseUser | None" = None,
        include_related: bool = True,
    ) -> PurgeReport:
        report = PurgeReport(dry_run=dry_run)

        ordered = expand_with_related(codes) if include_related else list(codes)

        for code in ordered:
            try:
                surface = get_surface(code)
            except KeyError:
                logger.warning("Skipping unknown cache surface: %s", code)
                continue

            result = SurfaceResult(code=code)

            for pattern in surface.django_patterns:
                matched = cache_instance.keys(pattern)
                safe, blocked = filter_protected(matched)
                result.django_matched += len(matched)
                result.django_blocked += len(blocked)
                if safe and not dry_run:
                    deleted = cache_instance.delete_raw_keys(safe)
                    # ``delete_raw_keys`` returns either an int or an
                    # awaitable depending on the Redis client; we
                    # always run it in sync mode but the type stubs
                    # don't narrow on configuration.
                    if isinstance(deleted, int):
                        result.django_deleted += deleted

            if surface.nuxt_patterns:
                nuxt_result = nuxt_client.request_purge(
                    list(surface.nuxt_patterns), dry_run=dry_run
                )
                result.nuxt_matched += nuxt_result.matched
                result.nuxt_deleted += nuxt_result.deleted
                result.nuxt_blocked += nuxt_result.blocked
                if nuxt_result.error:
                    result.nuxt_error = nuxt_result.error

            report.surfaces.append(result)

        CacheService._log_audit(report, actor=actor)
        return report

    @staticmethod
    def purge_all(
        *,
        dry_run: bool = False,
        actor: "AbstractBaseUser | None" = None,
    ) -> PurgeReport:
        codes = [s.code for s in iter_surfaces() if not s.danger]
        return CacheService.purge(
            codes,
            dry_run=dry_run,
            actor=actor,
            include_related=False,
        )

    @staticmethod
    def _log_audit(
        report: PurgeReport, *, actor: "AbstractBaseUser | None"
    ) -> None:
        try:
            from core.cache.models import CachePurgeLog
        except Exception:  # pragma: no cover — model unavailable
            return
        try:
            CachePurgeLog.objects.create(
                actor=actor if actor and actor.is_authenticated else None,
                surfaces=[s.code for s in report.surfaces],
                dry_run=report.dry_run,
                total_django=report.total_django,
                total_nuxt=report.total_nuxt,
                total_blocked=report.total_blocked,
                detail=[
                    {
                        "code": s.code,
                        "django_matched": s.django_matched,
                        "django_deleted": s.django_deleted,
                        "django_blocked": s.django_blocked,
                        "nuxt_matched": s.nuxt_matched,
                        "nuxt_deleted": s.nuxt_deleted,
                        "nuxt_blocked": s.nuxt_blocked,
                        "nuxt_error": s.nuxt_error,
                    }
                    for s in report.surfaces
                ],
            )
        except Exception as exc:  # pragma: no cover — audit failure
            logger.warning("Failed to write CachePurgeLog: %s", exc)
