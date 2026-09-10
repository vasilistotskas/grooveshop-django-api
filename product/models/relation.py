from __future__ import annotations

from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.models import SortableModel, TimeStampMixinModel
from product.enum.relation import RelationType


class ProductRelation(TimeStampMixinModel, SortableModel):
    """A merchant-curated, typed, directional link between two products.

    The Free tier of the recommendation engine is exactly this table:
    the ``curated`` strategy reads it and always wins over inferred
    candidates, because a merchant saying "show the pot with this
    plant" beats any similarity score. See
    ``docs/recommendations-engine.md`` §2.1 for why the relation is
    typed and why it is directional.

    ``SortableModel`` ordering is scoped per ``from_product`` — the
    merchant orders the relations OF one product, not the whole table —
    which is what the drag-and-drop inline on ``ProductAdmin`` edits.
    """

    from_product = models.ForeignKey(
        "product.Product",
        on_delete=models.CASCADE,
        related_name="relations",
        verbose_name=_("From product"),
    )
    to_product = models.ForeignKey(
        "product.Product",
        on_delete=models.CASCADE,
        related_name="related_from",
        verbose_name=_("To product"),
    )
    relation_type = models.CharField(
        _("Relation type"),
        max_length=20,
        choices=RelationType.choices,
        default=RelationType.SIMILAR,
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Product relation")
        verbose_name_plural = _("Product relations")
        ordering = ["from_product_id", "sort_order"]
        constraints = [
            models.UniqueConstraint(
                fields=["from_product", "to_product", "relation_type"],
                name="product_relation_unique_typed_pair",
            ),
            # A product related to itself is always a data-entry slip,
            # and the engine would have to special-case it on every read.
            models.CheckConstraint(
                condition=~Q(from_product=F("to_product")),
                name="product_relation_not_self",
            ),
        ]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            *SortableModel.Meta.indexes,
            BTreeIndex(
                fields=["from_product", "relation_type"],
                name="product_relation_from_type_ix",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.from_product_id} -{self.relation_type}-> "
            f"{self.to_product_id}"
        )

    def get_ordering_queryset(self) -> models.QuerySet[ProductRelation]:
        # Ordering is per source product: "which of THIS product's
        # relations comes first", not a global sequence.
        return ProductRelation.objects.filter(
            from_product_id=self.from_product_id
        )
