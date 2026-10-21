"""Compartment size derived from product dimensions.

Limits come from BOX NOW's FAQ (Small H8, Medium H17, Large H36, all
W45 x L60, cm); see ``shipping_boxnow.enum.compartment_size``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from extra_settings.models import Setting
from measurement.measures import Distance

from shipping.exceptions import ShippingParcelTooLargeError
from shipping_boxnow.enum import BoxNowCompartmentSize as Size
from shipping_boxnow.packing import (
    DEFAULT_SIZE_SETTING,
    default_compartment_size,
    derive_compartment,
    resolve_compartment_size,
    validate_default_compartment_size,
)


def _product(length=None, width=None, height=None):
    def cm(value):
        return None if value is None else Distance(cm=value)

    return SimpleNamespace(
        length=cm(length), width=cm(width), height=cm(height)
    )


def _size(*lines):
    return derive_compartment(lines).size


@pytest.mark.parametrize(
    ("height", "expected"),
    [
        (1, Size.SMALL),
        (8, Size.SMALL),
        (8.1, Size.MEDIUM),
        (17, Size.MEDIUM),
        (17.1, Size.LARGE),
        (36, Size.LARGE),
    ],
)
def test_height_boundaries(height, expected):
    assert _size((_product(60, 45, height), 1)) == expected


def test_one_millimetre_over_the_largest_height_fits_nothing():
    with pytest.raises(ShippingParcelTooLargeError):
        _size((_product(60, 45, 36.1), 1))


@pytest.mark.parametrize(
    "dimensions",
    [(61, 10, 5), (50, 50, 5), (46, 46, 10)],
)
def test_footprint_beyond_every_compartment_fits_nothing(dimensions):
    with pytest.raises(ShippingParcelTooLargeError):
        _size((_product(*dimensions), 1))


@pytest.mark.parametrize(
    "dimensions",
    [(60, 45, 8), (8, 60, 45), (45, 8, 60)],
)
def test_orientation_does_not_matter(dimensions):
    assert _size((_product(*dimensions), 1)) == Size.SMALL


def test_long_thin_item_lies_flat():
    """A 59 cm tube is 59 long, not 59 tall: it goes in a Small."""
    assert _size((_product(59, 6, 6), 1)) == Size.SMALL


def test_quantity_stacks_the_height():
    product = _product(30, 20, 5)
    assert _size((product, 1)) == Size.SMALL
    assert _size((product, 3)) == Size.MEDIUM
    assert _size((product, 7)) == Size.LARGE


def test_stack_taller_than_the_largest_compartment_fits_nothing():
    with pytest.raises(ShippingParcelTooLargeError):
        _size((_product(30, 20, 5), 8))


def test_lines_stack_together_and_share_the_widest_footprint():
    assert (
        _size((_product(30, 20, 5), 1), (_product(40, 40, 7), 1)) == Size.MEDIUM
    )


def test_no_dimensions_derives_nothing():
    derivation = derive_compartment([(_product(), 2)])
    assert derivation.size is None
    assert derivation.complete is False


@pytest.mark.parametrize(
    "product",
    [
        _product(length=30, width=20),
        _product(length=30, width=20, height=0),
    ],
)
def test_partial_or_zero_dimensions_count_as_missing(product):
    assert derive_compartment([(product, 1)]) == (None, False)


def test_empty_cart_derives_nothing():
    assert derive_compartment([]) == (None, True)


def test_ignores_lines_without_a_product_or_quantity():
    assert (
        derive_compartment([(None, 1), (_product(30, 20, 5), 0)]).size is None
    )


def _default_size(size: int) -> None:
    Setting.objects.update_or_create(
        name=DEFAULT_SIZE_SETTING,
        defaults={"value_type": "int", "value_int": size},
    )


@pytest.mark.django_db
def test_default_size_is_small_until_the_store_changes_it():
    assert default_compartment_size() == Size.SMALL
    _default_size(3)
    assert default_compartment_size() == Size.LARGE


@pytest.mark.django_db
def test_an_unreadable_default_size_names_the_setting():
    _default_size(7)
    with pytest.raises(ValueError, match=DEFAULT_SIZE_SETTING):
        default_compartment_size()


@pytest.mark.parametrize(
    ("value", "valid"),
    [(1, True), (3, True), ("2", True), (0, False), (4, False), ("x", False)],
)
def test_default_size_validator(value, valid):
    assert validate_default_compartment_size(value) is valid


@pytest.mark.django_db
def test_resolve_uses_the_store_default_without_dimensions():
    assert resolve_compartment_size([(_product(), 1)]) == Size.SMALL
    assert resolve_compartment_size(None) == Size.SMALL
    _default_size(2)
    assert resolve_compartment_size([(_product(), 1)]) == Size.MEDIUM


@pytest.mark.django_db
def test_resolve_ignores_the_default_when_every_item_is_measured():
    _default_size(3)
    assert resolve_compartment_size([(_product(30, 20, 5), 1)]) == Size.SMALL


@pytest.mark.django_db
def test_resolve_takes_the_larger_of_derived_and_default_for_a_mix():
    items = [(_product(30, 20, 12), 1), (_product(), 1)]
    assert resolve_compartment_size(items) == Size.MEDIUM
    _default_size(3)
    assert resolve_compartment_size(items) == Size.LARGE


@pytest.mark.django_db
def test_dimensions_survive_the_database_round_trip():
    """45 cm is stored as metres; the boundary must not drift."""
    from product.factories.product import ProductFactory
    from product.models.product import Product

    product = ProductFactory(num_images=0, num_reviews=0)
    Product.objects.filter(pk=product.pk).update(
        length=Distance(cm=60), width=Distance(cm=45), height=Distance(cm=17)
    )
    product = Product.objects.get(pk=product.pk)

    assert _size((product, 1)) == Size.MEDIUM
