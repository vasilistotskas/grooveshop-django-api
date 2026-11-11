"""Cached dashboard queries, one key each, cleared by the models they read.

A query is a function returning PLAIN data - numbers, codes, dates,
ids - decorated with ``@dashboard_query``. Widgets turn that data into
labels, URLs and chart JSON on every request, so nothing request- or
language-dependent is ever cached: the old single payload baked
translated labels and ``reverse()``d admin URLs (whose path is
translated) into a per-tenant blob that every later admin then saw in
the first viewer's language.

Rules:

- The key is ``admin:dashboard:<name>``; the tenant scope comes from the
  cache's ``KEY_FUNCTION`` (``tenant.cache.make_tenant_key``), and the
  prefix is what the "settings" purge surface already clears.
- A query whose data is translated content (a product's name) sets
  ``per_language``: one key per configured language.
- A query asked for one of several windows (the dashboard's period
  switch) lists them in ``variants`` and computes ``compute(variant)``:
  one key per variant, so a request computes only the window it shows
  and a write still clears every window.
- ``depends_on`` lists the models whose writes change the answer; each
  write clears exactly those keys, after the transaction commits so a
  read in between cannot re-cache the old answer. The TTL bounds
  everything a dependency does not name (new customers, carts, search
  logs).
- Bump the ``:vN`` suffix in a query's name when its data shape changes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.apps import apps
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.utils.translation import get_language

KEY_PREFIX = "admin:dashboard"


@dataclass(frozen=True)
class DashboardQuery:
    name: str
    ttl: int
    depends_on: tuple[str, ...]
    per_language: bool
    compute: Callable[..., Any]
    variants: tuple[str, ...] = ()

    def key(
        self, variant: str | None = None, language: str | None = None
    ) -> str:
        parts = [KEY_PREFIX, self.name]
        if self.variants:
            parts.append(self._variant(variant))
        if self.per_language:
            parts.append(language or get_language())
        return ":".join(parts)

    def all_keys(self) -> list[str]:
        variants: tuple[str | None, ...] = self.variants or (None,)
        languages: list[str | None] = (
            [code for code, _name in settings.LANGUAGES]
            if self.per_language
            else [None]
        )
        return [
            self.key(variant, language)
            for variant in variants
            for language in languages
        ]

    def get(self, variant: str | None = None) -> Any:
        if not self.variants:
            return cache.get_or_set(self.key(), self.compute, self.ttl)
        chosen = self._variant(variant)
        return cache.get_or_set(
            self.key(chosen), lambda: self.compute(chosen), self.ttl
        )

    def _variant(self, variant: str | None) -> str:
        if variant not in self.variants:
            raise ValueError(f"{self.name}: unknown variant {variant!r}")
        return variant


_QUERIES: list[DashboardQuery] = []


def dashboard_query(
    name: str,
    *,
    ttl: int,
    depends_on: tuple[str, ...] = (),
    per_language: bool = False,
    variants: tuple[str, ...] = (),
) -> Callable[[Callable[..., Any]], DashboardQuery]:
    def register(compute: Callable[..., Any]) -> DashboardQuery:
        query = DashboardQuery(
            name=name,
            ttl=ttl,
            depends_on=depends_on,
            per_language=per_language,
            compute=compute,
            variants=variants,
        )
        _QUERIES.append(query)
        return query

    return register


def keys_depending_on(label: str) -> list[str]:
    return [
        key
        for query in _QUERIES
        if label in query.depends_on
        for key in query.all_keys()
    ]


def _invalidator(label: str) -> Callable[..., None]:
    keys = keys_depending_on(label)

    def invalidate(*args: Any, **kwargs: Any) -> None:
        transaction.on_commit(lambda: cache.delete_many(keys))

    return invalidate


def connect_invalidation() -> None:
    """Connect every registered query's dependencies; idempotent."""
    labels = sorted({label for query in _QUERIES for label in query.depends_on})
    for label in labels:
        model = apps.get_model(label)
        receiver = _invalidator(label)
        for signal, event in ((post_save, "save"), (post_delete, "delete")):
            signal.connect(
                receiver,
                sender=model,
                dispatch_uid=f"admin.dashboard:{label}:{event}",
                weak=False,
            )
