from enum import StrEnum


class SearchTextSource(StrEnum):
    """A row whose name or value is part of its products' search text.

    The product document carries its brand's and category's name and its
    attributes' names and values. Each member names one such row and the
    ``Product`` lookup that finds the products using it.
    """

    BRAND = "brand"
    CATEGORY = "category"
    ATTRIBUTE = "attribute"
    ATTRIBUTE_VALUE = "attribute_value"

    @property
    def product_lookup(self) -> str:
        return _PRODUCT_LOOKUPS[self]


_PRODUCT_LOOKUPS = {
    SearchTextSource.BRAND: "brand_id",
    SearchTextSource.CATEGORY: "category_id",
    SearchTextSource.ATTRIBUTE: (
        "product_attributes__attribute_value__attribute_id"
    ),
    SearchTextSource.ATTRIBUTE_VALUE: "product_attributes__attribute_value_id",
}
