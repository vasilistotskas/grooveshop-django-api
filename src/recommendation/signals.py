"""Keep candidates fresh: a product or relation write schedules a
rebuild of the affected seed after the transaction commits.

Connected explicitly from ``RecommendationConfig.ready()`` with
``weak=False`` — the documented trap in ``src/meili/apps.py`` and
``src/core/celery.py``: a receiver that is not strongly referenced is
garbage-collected and the signal silently stops firing.

Dispatch goes through ``tenant.celery.dispatch_on_commit`` so the
tenant schema is captured at registration and stamped on the message;
by the time a commit hook fires the connection has usually snapped
back to ``public``.
"""

from __future__ import annotations

from django.db.models.signals import post_delete, post_save

from tenant.celery import dispatch_on_commit

# Writes that change nothing a strategy reads. ``view_count`` and
# ``click_score`` move on every visit and every search click; rebuilding
# candidates for each would be the query storm ``src/product/signals.py``
# already avoids for reindexing.
_NOISE_FIELDS = frozenset({"view_count", "click_score", "updated_at"})


def _schedule(product_id: int) -> None:
    from recommendation.tasks import recompute_candidates_for_product

    dispatch_on_commit(
        recompute_candidates_for_product, kwargs={"product_id": product_id}
    )


def on_product_saved(sender, instance, raw=False, update_fields=None, **kw):
    if raw:
        return
    if update_fields and set(update_fields) <= _NOISE_FIELDS:
        return
    _schedule(instance.pk)


def on_relation_changed(sender, instance, raw=False, **kwargs):
    # Both save and delete land here: either way the SOURCE product's
    # curated candidates are what changed.
    if raw:
        return
    _schedule(instance.from_product_id)


def on_order_confirmed(sender, order, **kwargs):
    # ``order_confirmed`` is sent once per order, when it is confirmed
    # (``OrderLifecycleService.confirm``): at creation for an order paid
    # offline, when its payment settles for a card order. A card order
    # nobody paid is no purchase, and is never attached. The task runs
    # after the commit, in the owning schema, and ties the order's lines
    # back to the impressions that showed them.
    from recommendation.tasks import record_recommendation_attach

    dispatch_on_commit(
        record_recommendation_attach, kwargs={"order_id": order.pk}
    )


def connect_signals() -> None:
    from order.signals import order_confirmed
    from product.models import Product, ProductRelation

    order_confirmed.connect(
        on_order_confirmed,
        dispatch_uid="recommendation.on_order_confirmed",
        weak=False,
    )

    post_save.connect(
        on_product_saved,
        sender=Product,
        dispatch_uid="recommendation.on_product_saved",
        weak=False,
    )
    post_save.connect(
        on_relation_changed,
        sender=ProductRelation,
        dispatch_uid="recommendation.on_relation_saved",
        weak=False,
    )
    post_delete.connect(
        on_relation_changed,
        sender=ProductRelation,
        dispatch_uid="recommendation.on_relation_deleted",
        weak=False,
    )
