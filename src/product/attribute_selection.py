"""The rule for a shopper's selected attribute values, in one place.

A shopper who ticks Black and White under "Colour" and 2 m under "Length"
means (Black OR White) AND 2 m: alternatives within one attribute, every
attribute narrowing the result. Every surface that filters products by
attribute value reads the selection through ``AttributeSelection``, so the
Meilisearch search (``search/views.py``) and the product list
(``product/filters/product.py``) cannot disagree about it again: a single
``attribute_values IN [...]`` or ``attribute_value__in`` over the whole
flat list is an OR across attributes, and returned the products that were
black OR 2 m long.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from product.models.attribute_value import AttributeValue
from product.models.product_attribute import ProductAttribute

if TYPE_CHECKING:
    from collections.abc import Iterable

    from django.db.models import QuerySet

    from meili.querysets import IndexQuerySet
    from product.models.product import Product, ProductTranslation

# The product index field that lists every attribute value of a product.
ATTRIBUTE_VALUES_FIELD = "attribute_values"


@dataclass(frozen=True, slots=True)
class AttributeSelection:
    """Selected attribute value ids, grouped by the attribute they belong to.

    ``by_attribute`` maps an attribute id to the selected ids among its
    values (each value belongs to exactly one attribute). ``unknown`` holds
    the ids that belong to no attribute, such as a deleted value in a
    stale link. They stay in the selection as a group of their own, which
    matches no product: a request for a value that does not exist filters
    to nothing instead of silently dropping the filter, as an unknown
    category does.
    """

    by_attribute: dict[int, tuple[int, ...]]
    unknown: tuple[int, ...]

    @classmethod
    def from_value_ids(cls, value_ids: Iterable[int]) -> AttributeSelection:
        selected = set(value_ids)
        by_attribute: dict[int, list[int]] = defaultdict(list)
        rows = (
            AttributeValue.objects.filter(id__in=selected)
            .order_by()
            .values_list("attribute_id", "id")
        )
        for attribute_id, value_id in rows:
            by_attribute[attribute_id].append(value_id)
        known = {value_id for ids in by_attribute.values() for value_id in ids}
        return cls(
            by_attribute={
                attribute_id: tuple(sorted(ids))
                for attribute_id, ids in sorted(by_attribute.items())
            },
            unknown=tuple(sorted(selected - known)),
        )

    @property
    def groups(self) -> list[tuple[int, ...]]:
        """One tuple of value ids per OR clause; the clauses are ANDed."""
        groups = list(self.by_attribute.values())
        if self.unknown:
            groups.append(self.unknown)
        return groups

    def sibling_value_ids(self) -> dict[int, frozenset[int]]:
        """Every value of each selected attribute, selected or not."""
        siblings: dict[int, set[int]] = defaultdict(set)
        rows = (
            AttributeValue.objects.filter(attribute_id__in=self.by_attribute)
            .order_by()
            .values_list("attribute_id", "id")
        )
        for attribute_id, value_id in rows:
            siblings[attribute_id].add(value_id)
        return {
            attribute_id: frozenset(ids)
            for attribute_id, ids in siblings.items()
        }

    def filter_products(self, queryset: QuerySet[Product]) -> QuerySet[Product]:
        """Keep the products that carry the selection (ORM)."""
        for ids in self.groups:
            queryset = queryset.filter(
                pk__in=ProductAttribute.objects.filter(
                    attribute_value_id__in=ids
                ).values("product_id")
            )
        return queryset

    def filter_search(
        self,
        search_qs: IndexQuerySet[ProductTranslation],
        *,
        with_counts: bool,
    ) -> IndexQuerySet[ProductTranslation]:
        """Keep the products that carry the selection (Meilisearch).

        One ``attribute_values IN [...]`` clause per attribute; Meilisearch
        ANDs the clauses of a filter list. ``with_counts`` says the caller
        reads the ``attribute_values`` facet: each attribute is then
        registered as a facet group, so its own values are counted without
        its own selection (the other attributes' selections still narrow
        them), and the shopper can see which alternatives a pick leaves.
        """
        siblings = self.sibling_value_ids() if with_counts else {}
        for attribute_id, selected in self.by_attribute.items():
            search_qs = search_qs.filter_facet_group(
                ATTRIBUTE_VALUES_FIELD,
                selected,
                members=siblings.get(attribute_id, ()),
            )
        if self.unknown:
            search_qs = search_qs.filter(
                **{f"{ATTRIBUTE_VALUES_FIELD}__in": list(self.unknown)}
            )
        return search_qs
