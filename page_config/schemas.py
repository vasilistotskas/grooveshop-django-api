"""Write-side validation for page sections and navigation seeds.

Section ``props`` and ``i18n`` validate against the JSON Schemas in
``page_config.section_schemas``, the canonical contract the storefront's
render-time parser is generated from. The two rules that span props stay
here as Python: a register's rows name declared sectors, and a
comparison table's rows are equally wide.

The navigation checks validate the item shapes the seed commands build
the relational menus from.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from django.conf import settings
from django.core.exceptions import ValidationError
from jsonschema import Draft202012Validator

from core.json_schema import _message
from core.utils.i18n import available_language_codes
from page_config.section_schemas import (
    SECTION_PROPS,
    i18n_schema,
    props_schema,
)


def _is_str(value, max_length: int) -> bool:
    return isinstance(value, str) and len(value) <= max_length


def _schema_errors(schema: dict, value: object) -> list[str]:
    return [
        _message(error)
        for error in sorted(
            Draft202012Validator(schema).iter_errors(value),
            key=lambda error: [str(part) for part in error.absolute_path],
        )
    ]


def _cross_check_project_register(props: dict) -> list[str]:
    """Every row's sector must be one of the declared sectors.

    The storefront resolves a row's pill colour by the sector's POSITION
    in ``sectors``, so a key that is not there loses its colour
    silently — cheap to get wrong by hand in the admin, invisible after.
    """
    declared = {sector["key"] for sector in props.get("sectors") or []}
    if not declared:
        return []
    unknown = sorted(
        {
            item["sector"]
            for item in props.get("items") or []
            # ``sector`` is optional: only a stated sector can be wrong.
            if item.get("sector") and item["sector"] not in declared
        }
    )
    if not unknown:
        return []
    return [f"items: sector(s) not declared in sectors: {unknown}"]


def _cross_check_comparison_rows(props: dict) -> list[str]:
    """Every row spans the same columns. A ragged row is refused rather
    than padded: a table whose rows disagree about the column count
    prints a value under the wrong heading."""
    widths = [len(row["values"]) for row in props.get("rows") or []]
    for index, width in enumerate(widths[1:], start=1):
        if width != widths[0]:
            return [
                (
                    f"rows[{index}].values: {width} values where the first "
                    f"row has {widths[0]} - every row spans the same columns"
                )
            ]
    return []


def _cross_check_hero_products(props: dict) -> list[str]:
    """A slide's ``product_id`` must name an active product of this store.

    "Active" is exactly what the storefront's product detail serves a
    visitor (``Product.objects.active()``), so an inactive or deleted
    product, which the chip would link to a 404, is refused. The chip is
    drawn from that product, so a dangling id would leave a
    slide pointing at nothing — and an id is the one thing a merchant
    types blind. One query for the whole carousel, run in the current
    tenant's schema, which is what makes "exists in this tenant" true.
    """
    wanted = {
        slide["product_id"]
        for slide in props.get("slides") or []
        if slide.get("product_id") is not None
    }
    if not wanted:
        return []
    from product.models.product import Product

    found = set(
        Product.objects.active()
        .filter(pk__in=wanted)
        .values_list("pk", flat=True)
    )
    missing = sorted(wanted - found)
    if not missing:
        return []
    return [
        f"slides: product_id(s) not found among this store's active products: {missing}"
    ]


# Run only once the props fit their schema: a cross-prop rule cannot
# say anything useful about a prop whose SHAPE is already wrong.
_CROSS_CHECKS: dict[str, Callable[[dict], list[str]]] = {
    "project_register": _cross_check_project_register,
    "comparison_table": _cross_check_comparison_rows,
    "hero_carousel": _cross_check_hero_products,
}


def _props_errors(component_type: str, props: object) -> list[str]:
    errors = _schema_errors(props_schema(component_type), props)
    cross = _CROSS_CHECKS.get(component_type)
    if not errors and cross is not None and isinstance(props, dict):
        errors = cross(props)
    return errors


def validate_section_props(component_type: str, props: object) -> None:
    """Raise ``ValidationError`` when ``props`` does not fit the section's
    contract. Unknown component types are the model field's problem
    (choices validation) and are skipped here."""
    if component_type not in SECTION_PROPS or props in (None, {}):
        return
    errors = _props_errors(component_type, props)
    if errors:
        raise ValidationError(
            f"Invalid props for {component_type}: " + "; ".join(errors)
        )


def validate_section_i18n(component_type: str, i18n: object) -> None:
    """Raise ``ValidationError`` when a section's per-locale overrides do
    not fit ``{"<locale>": {"title": str, "props": {...}}}`` — ``props``
    being a partial override checked against the same contract."""
    if component_type not in SECTION_PROPS or i18n in (None, {}):
        return
    errors = _schema_errors(i18n_schema(component_type), i18n)
    if not errors and isinstance(i18n, dict):
        for code, override in i18n.items():
            props = override.get("props")
            cross = _CROSS_CHECKS.get(component_type)
            if cross is not None and isinstance(props, dict):
                errors += [f"{code}.props.{e}" for e in cross(props)]
    if errors:
        raise ValidationError("Invalid i18n: " + "; ".join(errors))


_ICON_RE = re.compile(r"^i-[a-z0-9:-]+$")


def validate_icon_name(value: str) -> None:
    """Field validator for an ``@nuxt/icon`` name.

    Same rule the JSON menus are checked against, exposed as a field
    validator so the relational navigation models enforce it at the
    model layer rather than re-implementing the regex.
    """
    if value and not _ICON_RE.match(value):
        raise ValidationError(
            f"{value!r} is not an icon name — expected i-<collection>-<name>."
        )


def _check_nav_link(prefix: str, item: object) -> list[str]:
    if not isinstance(item, dict):
        return [f"{prefix}: must be an object"]
    errors: list[str] = []
    entry = {str(k): v for k, v in item.items()}
    label = entry.get("label")
    if not _is_str(label or "", 100) or not label:
        errors.append(f"{prefix}.label: required string ≤100")
    to = entry.get("to")
    href = entry.get("href")
    if bool(to) == bool(href):
        errors.append(f"{prefix}: exactly one of 'to' or 'href' required")
    if to is not None and (
        not _is_str(to, 1000) or not str(to).startswith("/")
    ):
        errors.append(f"{prefix}.to: must be an internal path")
    if href is not None and (
        not _is_str(href, 1000) or not str(href).startswith("https://")
    ):
        errors.append(f"{prefix}.href: must be an https URL")
    icon = entry.get("icon")
    if icon is not None and (
        not isinstance(icon, str) or not _ICON_RE.match(icon)
    ):
        errors.append(f"{prefix}.icon: must be an i-* icon name")
    unknown = set(entry) - {"label", "to", "href", "icon"}
    if unknown:
        errors.append(f"{prefix}: unknown keys {sorted(unknown)}")
    return errors


def validate_navigation_items(slot: str, items: object) -> None:
    """Raise ``ValidationError`` when a NavigationMenu payload doesn't
    fit its slot's shape."""
    if items in (None, []):
        return
    if not isinstance(items, list) or len(items) > 50:
        raise ValidationError("items must be a list of at most 50 entries.")

    errors: list[str] = []
    if slot == "footer":
        for i, raw in enumerate(items):
            if not isinstance(raw, dict):
                errors.append(f"[{i}]: must be an object")
                continue
            column = {str(k): v for k, v in raw.items()}
            label = column.get("label")
            if not _is_str(label or "", 100) or not label:
                errors.append(f"[{i}].label: required string ≤100")
            icon = column.get("icon")
            if icon is not None and (
                not isinstance(icon, str) or not _ICON_RE.match(icon)
            ):
                errors.append(f"[{i}].icon: must be an i-* icon name")
            children = column.get("children")
            if not isinstance(children, list) or not children:
                errors.append(f"[{i}].children: required non-empty list")
            else:
                for j, child in enumerate(children):
                    errors.extend(
                        _check_nav_link(f"[{i}].children[{j}]", child)
                    )
            unknown = set(column) - {"label", "icon", "children"}
            if unknown:
                errors.append(f"[{i}]: unknown keys {sorted(unknown)}")
    else:
        for i, item in enumerate(items):
            errors.extend(_check_nav_link(f"[{i}]", item))

    if errors:
        raise ValidationError(
            f"Invalid {slot} navigation: " + "; ".join(errors)
        )


def _check_locale_keys(i18n: object, label: str) -> dict:
    """Shared shape check for the two per-locale override fields.

    The default locale is NOT a valid key: its values are the columns
    above the override (``props``/``items``), so accepting it here would
    give one locale two sources of truth and no rule for which wins.
    """
    if not isinstance(i18n, dict):
        raise ValidationError(f"{label} must be a JSON object.")

    codes = available_language_codes()
    default = settings.PARLER_DEFAULT_LANGUAGE_CODE
    errors: list[str] = []
    for code in i18n:
        if not isinstance(code, str) or code not in codes:
            errors.append(f"{code!r}: not a language this store serves")
        elif code == default:
            errors.append(
                f"{code!r}: is the default locale — edit the fields "
                "themselves rather than overriding them"
            )
    if errors:
        raise ValidationError(f"Invalid {label}: " + "; ".join(errors))
    return {str(k): v for k, v in i18n.items()}


def validate_navigation_i18n(slot: str, i18n: object) -> None:
    """Raise ``ValidationError`` when a menu's per-locale overrides
    don't fit ``{"<locale>": [...items...]}``.

    A menu is translated WHOLE rather than per-item: the items are a
    list, so an index-keyed overlay would silently retarget every label
    the first time an operator reorders the menu.
    """
    if i18n in (None, {}):
        return

    for code, items in _check_locale_keys(i18n, "i18n").items():
        try:
            validate_navigation_items(slot, items)
        except ValidationError as exc:
            raise ValidationError(
                f"i18n.{code}: " + "; ".join(exc.messages)
            ) from exc
