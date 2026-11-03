"""IndexNow: which pages are waiting, which URLs they become, and the POST.

Protocol (https://www.indexnow.org/documentation): a POST of JSON
``{"host", "key", "urlList"}`` to a search engine's endpoint; every URL is on
``host``; the key is proven by the public file ``https://<host>/<key>.txt``
(the storefront serves it from ``tenant/resolve``); at most 10,000 URLs per
POST; 200 and 202 are success, 400/403/422/429 are the refusals.

A store's changes are queued as :class:`~indexnow.models.PendingUrl` rows by
``indexnow.signals`` and sent in one POST per run by
``indexnow.tasks.submit_pending_urls``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import requests
from django.conf import settings
from django.db import connection
from django.utils import timezone
from django_tenants.utils import get_public_schema_name, schema_context

from core.retry import is_permanent_http_failure
from core.store_settings import store_setting
from core.utils.tenant_urls import (
    get_tenant_frontend_url,
    tenant_storefront_locales,
)
from indexnow.models import PendingUrl
from tenant.membership import bound_tenant

if TYPE_CHECKING:
    from tenant.models import Tenant

logger = logging.getLogger(__name__)

#: The protocol's limit on one POST.
MAX_URLS_PER_REQUEST = 10_000

ENABLED_SETTING = "INDEXNOW_ENABLED"


@dataclass(frozen=True, slots=True)
class Submission:
    """What one run did for a store."""

    paths: int = 0
    urls: int = 0


def is_enabled() -> bool:
    """Whether the store whose schema is active has IndexNow on. The
    control plane is not a store: it never has."""
    if connection.schema_name == get_public_schema_name():
        return False
    return bool(store_setting(ENABLED_SETTING))


def published_key(tenant: Tenant) -> str:
    """The key the storefront serves at ``/<key>.txt``: the store's, while
    its IndexNow switch is on, otherwise nothing (no file is served)."""
    if tenant.schema_name == get_public_schema_name():
        return ""
    with schema_context(tenant.schema_name):
        return tenant.indexnow_key if store_setting(ENABLED_SETTING) else ""


def queue(paths: Iterable[str]) -> None:
    """Remember that these storefront paths changed. A path already waiting
    is brought up to date, not repeated."""
    now = timezone.now()
    PendingUrl.objects.bulk_create(
        [PendingUrl(path=path, queued_at=now) for path in dict.fromkeys(paths)],
        update_conflicts=True,
        unique_fields=["path"],
        update_fields=["queued_at"],
    )


def notification_urls(paths: Iterable[str]) -> list[str]:
    """Each path as the canonical storefront URL in every language the
    bound store serves, on its primary domain (the locale prefix is the
    one ``get_tenant_frontend_url`` puts on an email link)."""
    languages = tenant_storefront_locales(bound_tenant())
    return [
        get_tenant_frontend_url(path, language=language)
        for path in paths
        for language in languages
    ]


def post(host: str, key: str, urls: list[str]) -> None:
    """POST *urls* to the IndexNow endpoint.

    200 and 202 (received, the key still being validated) are success;
    every refusal is a 4xx and raises ``requests.HTTPError``, so the
    calling task's retry rules decide (``core.retry``: a 5xx, 408 and 429
    are retried, any other 4xx is not).
    """
    response = requests.post(
        settings.INDEXNOW_ENDPOINT,
        json={"host": host, "key": key, "urlList": urls},
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=settings.INDEXNOW_TIMEOUT_SECONDS,
    )
    response.raise_for_status()


def send_pending() -> Submission:
    """Send the bound store's waiting pages in one POST and forget them.

    A store that switched IndexNow off drops its queue instead, so that
    switching it on later does not announce changes from months ago. A
    refusal that cannot succeed on a second attempt (``core.retry``) drops
    the batch too, and is raised: the same URLs would be refused again on
    every run. Anything else raises with the batch kept for the next try.
    A page that changed again while the POST was in flight stays queued.
    """
    tenant = bound_tenant()
    if tenant is None:
        return Submission()
    if not is_enabled():
        PendingUrl.objects.all().delete()
        return Submission()
    primary = tenant.domains.filter(is_primary=True).first()
    if primary is None:
        logger.warning(
            "IndexNow: the store has no primary domain, nothing was sent",
            extra={"schema": tenant.schema_name},
        )
        return Submission()
    started = timezone.now()
    languages = len(tenant_storefront_locales(tenant))
    batch = list(
        PendingUrl.objects.filter(queued_at__lte=started).order_by(
            "queued_at", "pk"
        )[: MAX_URLS_PER_REQUEST // languages]
    )
    if not batch:
        return Submission()
    urls = notification_urls(row.path for row in batch)
    try:
        post(primary.domain, tenant.indexnow_key, urls)
    except requests.HTTPError as error:
        if is_permanent_http_failure(error):
            _forget(batch, started)
            logger.error(
                "IndexNow refused the notification and it will not be repeated",
                extra={
                    "schema": tenant.schema_name,
                    "status": error.response.status_code,
                    "urls": len(urls),
                },
            )
        raise
    _forget(batch, started)
    logger.info(
        "IndexNow notified of %s URLs",
        len(urls),
        extra={"schema": tenant.schema_name, "paths": len(batch)},
    )
    return Submission(paths=len(batch), urls=len(urls))


def _forget(batch: list[PendingUrl], started: datetime) -> None:
    PendingUrl.objects.filter(
        pk__in=[row.pk for row in batch], queued_at__lte=started
    ).delete()
