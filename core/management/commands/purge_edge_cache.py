"""Purge every storefront page from the Cloudflare edge cache.

Run by the post-deploy hook, BEFORE the storefront cache warm-up: a page
cached at the edge by the previous build references ``/_nuxt`` chunks the
new build no longer serves, so it must not outlive the deploy. The
per-edit purges go through ``CacheService`` instead
(``core.cache.edge.schedule_purge``).

Fails (exit code 1) when a zone could not be purged, so the hook reports
it rather than leaving old pages at the edge unnoticed.
"""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand, CommandError

from core.cache import edge

_ATTEMPTS = 4
# Cloudflare's Free plan refills five purges a minute.
_BACKOFF_SECONDS = 15


class Command(BaseCommand):
    help = "Purge every storefront page from the Cloudflare edge cache."

    def handle(self, *args, **options) -> None:
        for attempt in range(1, _ATTEMPTS + 1):
            try:
                zones = edge.purge_all_storefronts()
            except edge.EdgePurgeError as exc:
                if attempt == _ATTEMPTS:
                    raise CommandError(str(exc)) from exc
                self.stderr.write(f"{exc}; retrying")
                time.sleep(_BACKOFF_SECONDS * attempt)
                continue
            self.stdout.write(f"edge cache purged: {zones} zone(s)")
            return
