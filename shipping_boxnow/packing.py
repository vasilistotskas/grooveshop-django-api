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
does to the size is decided by ``resolve_compartment_size`` from the
store's ``BOXNOW_DEFAULT_COMPARTMENT_SIZE``; ``derive_compartment`` only
reports it.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, NamedTuple

from django.conf import settings

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


DEFAULT_SIZE_SETTING = "BOXNOW_DEFAULT_COMPARTMENT_SIZE"


def validate_default_compartment_size(value: object) -> bool:
    """django-extra-settings validator for ``BOXNOW_DEFAULT_COMPARTMENT_SIZE``."""
    try:
        BoxNowCompartmentSize(int(str(value)))
    except ValueError:
        return False
    return True


def default_compartment_size() -> BoxNowCompartmentSize:
    """The store's size for products that carry no dimensions."""
    from extra_settings.models import Setting

    default = next(
        entry["value"]
        for entry in settings.EXTRA_SETTINGS_DEFAULTS
        if entry["name"] == DEFAULT_SIZE_SETTING
    )
    raw = Setting.get(DEFAULT_SIZE_SETTING, default=default)
    try:
        return BoxNowCompartmentSize(int(raw))
    except ValueError as exc:
        raise ValueError(
            f"{DEFAULT_SIZE_SETTING} is not a BoxNow compartment size: {raw!r}"
        ) from exc


def resolve_compartment_size(
    items: Iterable[tuple[Any, int]] | None,
) -> BoxNowCompartmentSize:
    """The compartment size to book for ``items``.

    Where the products carry no dimensions the size is the store's
    ``BOXNOW_DEFAULT_COMPARTMENT_SIZE`` (Small unless changed), and where
    only some do it is a floor: derived from the measured items alone,
    the size could be too small for the unmeasured ones. When every item
    is measured the derived size is the answer and the setting is ignored.
    """
    derivation = derive_compartment(items or [])
    if derivation.size is None:
        return default_compartment_size()
    if derivation.complete:
        return derivation.size
    return max(derivation.size, default_compartment_size())
