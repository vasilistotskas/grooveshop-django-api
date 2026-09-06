"""``weight_info`` must always be an object, because the schema says so.

``CartItemSerializer.get_weight_info`` carries an ``@extend_schema_field``
declaring ``unit_weight``/``total_weight``/``weight_unit`` all required,
and the parent schema lists ``weightInfo`` itself as required. The
generated client honours that literally — ``shared/openapi/zod.gen.ts``
has ``weightInfo: z.object({...})`` with no ``.nullable()``.

The serializer used to return ``None`` for a falsy weight. ``Product.weight``
is non-nullable with ``default=zero_weight``, so it is never None — but
``MeasureBase.__bool__`` is ``bool(self.standard)``, which makes a ZERO
weight falsy. Any product left at the default therefore serialised
``weight_info: null`` against a non-nullable contract, and ``parseDataAs``
rejected the entire cart response rather than one field.
"""

import pytest

from cart.factories.item import CartItemFactory
from cart.serializers.item import CartItemSerializer
from product.factories import ProductFactory


@pytest.mark.django_db
class TestWeightInfoIsAlwaysAnObject:
    def test_zero_weight_product_still_serialises_an_object(self):
        """The regression itself: default weight is falsy, not absent."""
        from measurement.measures import Weight

        product = ProductFactory(weight=Weight(kg=0))
        assert not product.weight, (
            "sanity: a zero weight must be FALSY - that is what made the "
            "old `if product.weight:` skip it"
        )
        assert product.weight is not None, (
            "sanity: the field is non-nullable, so it is never None"
        )

        item = CartItemFactory(product=product, quantity=3)
        info = CartItemSerializer(item).data["weight_info"]

        assert info is not None, (
            "null violates the declared schema and fails zod on the client"
        )
        assert set(info) == {"unit_weight", "total_weight", "weight_unit"}
        assert info["unit_weight"] == 0.0
        assert info["total_weight"] == 0.0
        assert info["weight_unit"]

    def test_non_zero_weight_still_multiplies_by_quantity(self):
        """The fix must not flatten real weights to zero."""
        from measurement.measures import Weight

        product = ProductFactory(weight=Weight(kg=2))
        item = CartItemFactory(product=product, quantity=4)

        info = CartItemSerializer(item).data["weight_info"]

        assert info["unit_weight"] == 2.0
        assert info["total_weight"] == 8.0

    def test_declared_schema_requires_the_object(self):
        """Pins the contract this serialiser has to satisfy.

        If someone makes the field nullable in the decorator instead of
        fixing the value, this fails and points at the real decision:
        the frontend needs the numbers for shipping, so null only moves
        the crash downstream.
        """
        # ``extend_schema_field`` annotates the METHOD, not the field.
        declared = CartItemSerializer.get_weight_info._spectacular_annotation[
            "field"
        ]

        assert declared["type"] == "object"
        assert set(declared["required"]) == {
            "unit_weight",
            "total_weight",
            "weight_unit",
        }
        assert "nullable" not in declared
