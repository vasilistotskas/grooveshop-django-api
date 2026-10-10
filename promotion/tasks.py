from __future__ import annotations

import logging

from core import celery_app
from core.tasks import MonitoredTask

logger = logging.getLogger(__name__)


@celery_app.task(
    base=MonitoredTask,
    max_retries=5,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def reindex_offer_products_task() -> dict[str, int]:
    """Reindex the products whose promotions changed, in one pass.

    Queued by ``promotion.offer_sync.queue_reindex`` a few seconds after
    the first change, so every change made meanwhile rides along.
    """
    from product.signals import reindex_products_by_pk
    from promotion.offer_sync import drain_pending

    product_ids = drain_pending()
    documents = reindex_products_by_pk(product_ids)
    logger.info(
        "Offer reindex: %s products, %s documents",
        len(product_ids),
        documents,
    )
    return {"products": len(product_ids), "documents": documents}


@celery_app.task(
    base=MonitoredTask,
    max_retries=5,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def reindex_offer_window_changes_task() -> dict[str, int]:
    """Reindex the products a promotion window just opened or closed on.

    Nothing is saved when ``starts_at`` / ``ends_at`` passes, so no signal
    fires; this beat task is what notices.
    """
    from promotion.offer_sync import reindex_window_changes

    product_ids = reindex_window_changes()
    logger.info("Offer window sweep: %s products", len(product_ids))
    return {"products": len(product_ids)}
