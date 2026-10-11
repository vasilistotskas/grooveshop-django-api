"""A small attribute catalogue for the attribute-value filter tests.

Three attributes with two values each, and six products spread over them
so that every selection below has a different answer, and a selection read
as one flat OR list (the bug) differs from the right one:

    product  colour  length  material
    p1       black   2 m     cotton
    p2       black   1 m     wool
    p3       black   1 m     cotton
    p4       white   2 m     wool
    p5       white   2 m     cotton
    p6       white   1 m     wool

Selecting black AND 2 m is {p1}; an OR over the flat list is
{p1..p5}.
"""

from __future__ import annotations

from product.factories.attribute import AttributeFactory
from product.factories.attribute_value import AttributeValueFactory
from product.factories.product_attribute import ProductAttributeFactory
from product.models.attribute_value import AttributeValue
from product.models.product import Product

# product -> (colour, length, material), the keys of ``create_values``.
GRID: dict[str, tuple[str, str, str]] = {
    "p1": ("black", "two_m", "cotton"),
    "p2": ("black", "one_m", "wool"),
    "p3": ("black", "one_m", "cotton"),
    "p4": ("white", "two_m", "wool"),
    "p5": ("white", "two_m", "cotton"),
    "p6": ("white", "one_m", "wool"),
}

_ATTRIBUTE_VALUES = {
    "colour": ("black", "white"),
    "length": ("one_m", "two_m"),
    "material": ("cotton", "wool"),
}


def create_values() -> dict[str, AttributeValue]:
    """The six attribute values, keyed by the names used in ``GRID``."""
    values: dict[str, AttributeValue] = {}
    for keys in _ATTRIBUTE_VALUES.values():
        attribute = AttributeFactory(active=True)
        for key in keys:
            values[key] = AttributeValueFactory(
                attribute=attribute, active=True
            )
    return values


def assign_values(
    product: Product, key: str, values: dict[str, AttributeValue]
) -> None:
    """Give ``product`` the attribute values ``GRID`` lists for ``key``."""
    for value_key in GRID[key]:
        ProductAttributeFactory(
            product=product, attribute_value=values[value_key]
        )
