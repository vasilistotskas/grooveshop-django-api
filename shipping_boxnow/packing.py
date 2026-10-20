"""Which BOX NOW compartment an order's items need.

The rule is deliberately simple and errs towards the larger size, never
the smaller (a parcel that does not fit its compartment is refused at
the locker):

* Each unit is laid down on its smallest side, so its height is its
  smallest dimension and its footprint the other two.
* The footprint must fit the compartment's width x length. Every size
  shares the same footprint, so one unit that does not fit it fits no
  size.
* Units are stacked, never placed side by side: the parcel's height is
  the sum of every unit's height. Side-by-side packing could save a
  size for small items, but it would also trust a packing nobody
  verifies.
* The result is the smallest size whose height holds the stack.

Only items with all three dimensions count. What a missing dimension
does to the size is the caller's decision (``derive_compartment``
reports it), because the current behaviour for that case belongs to the
checkout payload, not to the packing arithmetic.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, NamedTuple

from shipping.exceptions import ShippingParcelTooLargeError
from shipping_boxnow.enum import COMPARTMENT_LIMITS_CM, BoxNowCompartmentSize

# Product dimensions are stored as float metres: 45 cm comes back as
# 45.00000000000001 cm. Compared at millimetre precision instead.
_CM_DECIMALS = 1


class Derivation(NamedTuple):
    """The outcome of :func:`derive_compartment`.

    ``size`` is ``None`` when no item carries dimensions (nothing to
    derive from). ``complete`` is ``False`` when at least one item
    lacks them, so ``size`` only covers the measured items.
    """

    size: BoxNowCompartmentSize | None
    complete: bool


def _centimetres(distance: Any) -> float | None:
    if distance is None:
        return None
    value = round(float(distance.cm), _CM_DECIMALS)
    return value if value > 0 else None


def _sorted_dimensions_cm(product: Any) -> tuple[float, float, float] | None:
    """The product's ``(height, short side, long side)`` or ``None``."""
    length = _centimetres(getattr(product, "length", None))
    width = _centimetres(getattr(product, "width", None))
    height = _centimetres(getattr(product, "height", None))
    if length is None or width is None or height is None:
        return None
    smallest, middle, largest = sorted((length, width, height))
    return smallest, middle, largest


def derive_compartment(
    items: Iterable[tuple[Any, int]],
) -> Derivation:
    """The smallest compartment holding the items that have dimensions.

    ``items`` are ``(product, quantity)`` pairs, as the shipment-row hook
    receives them.

    Raises :class:`~shipping.exceptions.ShippingParcelTooLargeError` when
    the measured items fit no compartment.
    """
    stack_height = 0.0
    measured = False
    complete = True
    widest_footprint = (0.0, 0.0)

    for product, quantity in items:
        if not product or not quantity:
            continue
        dimensions = _sorted_dimensions_cm(product)
        if dimensions is None:
            complete = False
            continue
        measured = True
        height, short_side, long_side = dimensions
        stack_height += height * quantity
        widest_footprint = (
            max(widest_footprint[0], short_side),
            max(widest_footprint[1], long_side),
        )

    if not measured:
        return Derivation(size=None, complete=complete)

    for size in BoxNowCompartmentSize:
        limits = COMPARTMENT_LIMITS_CM[size]
        short_limit, long_limit = sorted((limits.width, limits.length))
        if (
            round(stack_height, _CM_DECIMALS) <= limits.height
            and widest_footprint[0] <= short_limit
            and widest_footprint[1] <= long_limit
        ):
            return Derivation(size=size, complete=complete)

    largest = COMPARTMENT_LIMITS_CM[max(BoxNowCompartmentSize)]
    raise ShippingParcelTooLargeError(
        detail=(
            f"stacked height {stack_height:g} cm and footprint "
            f"{widest_footprint[0]:g} x {widest_footprint[1]:g} cm exceed "
            f"the largest compartment ({largest.height:g} x "
            f"{largest.width:g} x {largest.length:g} cm)."
        ),
    )


def resolve_compartment_size(
    items: Iterable[tuple[Any, int]] | None,
    *,
    requested: int,
) -> int:
    """The compartment size to book for ``items``.

    ``requested`` is the size the checkout sent (``boxnow_compartment_size``,
    1 = Small when the storefront sends nothing). It is the size used
    where the products carry no dimensions, and a floor where only some
    do: derived from the measured items alone, the size could be too
    small for the unmeasured ones. When every item is measured the
    derived size is the answer and ``requested`` is ignored.
    """
    if items is None:
        return requested
    derivation = derive_compartment(items)
    if derivation.size is None:
        return requested
    if derivation.complete:
        return derivation.size
    return max(derivation.size, requested)
