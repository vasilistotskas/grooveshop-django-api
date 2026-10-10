"""Reindex ``on_offer`` when a promotion change can move it.

Everything routes through ``promotion.offer_sync.queue_reindex``, which
coalesces: an admin save that touches the row and four M2M sets fires
five signals and runs one reindex.

Two kinds of mark, because a removed scope cannot be recomputed after
the fact. A change that leaves the new scope readable records the
promotion id (the task resolves its products later). A change that
destroys the old scope — deleting the promotion, removing or clearing
members — records the product ids NOW, while the rows still exist.

Not covered: the reverse side of the M2M (``product.promotions.add``) and
the merchant's ``PROMOTIONS_ENABLED`` toggle; the nightly Meilisearch
sync repairs both.
"""

from __future__ import annotations

from django.db.models import Count
from django.db.models.signals import (
    m2m_changed,
    post_delete,
    post_save,
    pre_delete,
)
from django.dispatch import receiver

from promotion.models import Promotion, PromotionRedemption
from promotion.offer_sync import indexing_enabled, queue_reindex
from promotion.offers import scope_product_ids

_SCOPE_THROUGHS = (
    Promotion.products.through,
    Promotion.categories.through,
    Promotion.excluded_products.through,
    Promotion.excluded_categories.through,
)


@receiver(
    post_save,
    sender=Promotion,
    dispatch_uid="promotion.reindex_offer_on_save",
)
def reindex_offer_on_promotion_save(sender, instance, raw=False, **kwargs):
    if raw:
        return
    queue_reindex(promotion_ids=[instance.pk])


@receiver(
    pre_delete,
    sender=Promotion,
    dispatch_uid="promotion.reindex_offer_on_delete",
)
def reindex_offer_on_promotion_delete(sender, instance, **kwargs):
    if not indexing_enabled():
        return
    queue_reindex(product_ids=scope_product_ids([instance.pk]))


def reindex_offer_on_scope_change(sender, instance, action, reverse, **kwargs):
    if reverse or not indexing_enabled():
        return
    if action in {"pre_remove", "pre_clear"}:
        queue_reindex(product_ids=scope_product_ids([instance.pk]))
    elif action in {"post_add", "post_remove", "post_clear"}:
        queue_reindex(promotion_ids=[instance.pk])


for _through in _SCOPE_THROUGHS:
    m2m_changed.connect(
        reindex_offer_on_scope_change,
        sender=_through,
        dispatch_uid=f"promotion.reindex_offer_on_scope.{_through.__name__}",
    )


def _crossed_usage_limit(promotion_id: int, *, created: bool) -> bool:
    """Whether this redemption moved the promotion across its total limit.

    ``publicly_listable`` stops advertising a promotion once redemptions
    reach ``usage_limit_total``; only the redemption that makes them equal
    (or the deletion that drops them back below) changes the answer.
    """
    row = (
        Promotion.objects.filter(
            pk=promotion_id, usage_limit_total__isnull=False
        )
        .annotate(used=Count("redemptions", distinct=True))
        .values_list("usage_limit_total", "used")
        .first()
    )
    if row is None:
        return False
    limit, used = row
    return used == limit if created else used == limit - 1


@receiver(
    post_save,
    sender=PromotionRedemption,
    dispatch_uid="promotion.reindex_offer_on_redemption",
)
def reindex_offer_on_redemption(sender, instance, created, raw=False, **kwargs):
    if raw or not created or not indexing_enabled():
        return
    if _crossed_usage_limit(instance.promotion_id, created=True):
        queue_reindex(promotion_ids=[instance.promotion_id])


@receiver(
    post_delete,
    sender=PromotionRedemption,
    dispatch_uid="promotion.reindex_offer_on_redemption_delete",
)
def reindex_offer_on_redemption_delete(sender, instance, **kwargs):
    if indexing_enabled() and _crossed_usage_limit(
        instance.promotion_id, created=False
    ):
        queue_reindex(promotion_ids=[instance.promotion_id])
