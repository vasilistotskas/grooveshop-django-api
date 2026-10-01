"""Postal-address rules shared by every address write path.

The postcode format is data, not code: ``Country.postal_code_pattern``
holds Google's Address Data Service ``zip`` regex for the country, and
the storefront applies the same row's pattern inline at checkout
(``shared/utils/postalCode.ts``). Both sides normalise the same way
before matching, so a postcode one accepts the other accepts too.

Callers:

- ``OrderCreateFromCartSerializer`` / ``OrderWriteSerializer``
- ``UserAddressWriteSerializer``
- ``Order.clean()`` / ``UserAddress.clean()`` (admin edits)
- the ACS voucher builder, which refuses to send a postcode that does
  not match instead of blanking it.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

if TYPE_CHECKING:
    from django_stubs_ext import StrOrPromise

    from country.models import Country
    from region.models import Region

_WHITESPACE_RUN = re.compile(r"\s+")

# A street number this long that also parses as the country's postcode
# is a postcode typed into the wrong field (prod order #316: "70300").
# Four, not five: Cyprus postcodes are four digits.
_MIN_POSTCODE_LOOKALIKE_LENGTH = 4


def normalize_postcode(value: str) -> str:
    """Return the canonical form: trimmed, upper-case, single spaces.

    Internal whitespace is collapsed rather than removed because some
    of Google's patterns require the space (``FIQQ 1ZZ``, ``LIMA 23``);
    the patterns that allow it make it optional (``\\d{3} ?\\d{2}``), so
    both ``70300`` and ``703 00`` stay valid Greek postcodes.
    """
    return _WHITESPACE_RUN.sub(" ", value).strip().upper()


@lru_cache(maxsize=256)
def _compiled(pattern: str) -> re.Pattern[str]:
    # ASCII, like the storefront's (flag-less) JavaScript RegExp: ``\d``
    # must not accept non-ASCII digits one side would reject.
    return re.compile(pattern, re.ASCII)


def postcode_matches(country: Country, value: str) -> bool:
    """True when ``value`` is a valid postcode for ``country``.

    An empty value never matches. A country without a pattern accepts
    any non-empty value: there is no format to check against.
    """
    postcode = normalize_postcode(value)
    if not postcode:
        return False
    pattern = country.postal_code_pattern
    if not pattern:
        return True
    return _compiled(pattern).fullmatch(postcode) is not None


def postcode_error(country: Country) -> StrOrPromise:
    """The shopper-facing message for a postcode ``country`` rejects."""
    if country.postal_code_example:
        return _("Enter a valid postcode, e.g. %(example)s.") % {
            "example": country.postal_code_example
        }
    return _("Enter a valid postcode.")


def address_errors(
    *,
    country: Country,
    street: str,
    street_number: str,
    zipcode: str,
    region: Region | None = None,
) -> dict[str, list[StrOrPromise]]:
    """Field errors for a delivery address, keyed by model field name.

    Five rules, the first three aimed at the mix-up that let order #316
    through (street "1", street number "70300", a non-numeric postcode):

    - the postcode matches the country's format;
    - the street name contains a letter, so a bare number is rejected;
    - the street number is not itself a postcode of that country;
    - a region is required exactly when the country has any — added
      for the full ISO 3166-1 seed, most of which have none, so a
      region can no longer be unconditionally required at the field
      level (``UserAddressWriteSerializer.region`` is
      ``required=False``; this validator is what actually enforces
      it, per-country);
    - the region, when given, actually belongs to the country — added
      for Cyprus, where a Greek region picked by a stale client would
      otherwise pair silently with a Cypriot address (this also covers
      "a region was given for a country that has none": no region row
      can belong to a country with zero of them).
    """
    errors: dict[str, list[StrOrPromise]] = {}

    if not postcode_matches(country, zipcode):
        errors["zipcode"] = [postcode_error(country)]

    if street and not any(char.isalpha() for char in street):
        errors["street"] = [
            _(
                "Enter the street name here; the number goes in the "
                "street number field."
            )
        ]

    number = normalize_postcode(street_number)
    if (
        country.postal_code_pattern
        and len(number.replace(" ", "")) >= _MIN_POSTCODE_LOOKALIKE_LENGTH
        and postcode_matches(country, number)
    ):
        errors["street_number"] = [
            _(
                "This looks like a postcode. Enter the street number "
                "here and the postcode in the postcode field."
            )
        ]

    if region is None:
        if country.regions.exists():
            errors["region"] = [_("Select a region for the selected country.")]
    elif region.country_id != country.alpha_2:
        errors["region"] = [
            _("Select a region that belongs to the selected country.")
        ]

    return errors


ADDRESS_FIELDS = ("country", "region", "street", "street_number", "zipcode")


def address_update_errors(
    attrs: dict[str, Any], instance: object | None
) -> dict[str, list[StrOrPromise]]:
    """Serializer-side rules for an address write over a stored row.

    ``attrs`` (validated serializer data) is merged over ``instance``.
    The address is judged only when that changes it, so a write that
    leaves the address alone does not re-judge one that predates the
    rules. On success the postcode in ``attrs`` is normalised in place.
    """
    stored = (
        {field: getattr(instance, field) for field in ADDRESS_FIELDS}
        if instance is not None
        else {}
    )
    address = {
        field: attrs.get(field, stored.get(field)) for field in ADDRESS_FIELDS
    }
    changed = any(
        field not in stored or address[field] != stored[field]
        for field in ADDRESS_FIELDS
    )
    if not changed or address["country"] is None:
        return {}
    errors = address_errors(
        country=address["country"],
        street=address["street"],
        street_number=address["street_number"],
        zipcode=address["zipcode"],
        region=address["region"],
    )
    if not errors:
        attrs["zipcode"] = normalize_postcode(address["zipcode"])
    return errors


def model_address_errors(instance: Any) -> dict[str, list[StrOrPromise]]:
    """``Model.clean()``-side rules for an ``Order`` / ``UserAddress``.

    Judged only when the address differs from the stored row (or there
    is none yet), so an admin saving an old record for an unrelated
    reason is not blocked by an address that predates the rules. On
    success the instance's postcode is normalised in place.
    """
    if instance.country_id is None:
        return {}
    fields = ("country_id", "region_id", "street", "street_number", "zipcode")
    stored = (
        type(instance)
        ._base_manager.filter(pk=instance.pk)
        .values(*fields)
        .first()
        if instance.pk
        else None
    )
    if stored is not None and all(
        getattr(instance, field) == stored[field] for field in fields
    ):
        return {}
    errors = address_errors(
        country=instance.country,
        street=instance.street,
        street_number=instance.street_number,
        zipcode=instance.zipcode,
        region=instance.region,
    )
    if not errors:
        instance.zipcode = normalize_postcode(instance.zipcode)
    return errors


# The stored pattern also runs in the storefront, as a JavaScript RegExp
# without the ``u`` flag (``shared/utils/postalCode.ts``). Python accepts
# syntax JavaScript rejects (named ``(?P<…>)`` groups, inline flags) or
# reads differently (``\A``/``\Z`` as letters, ``{,3}`` and an unclosed
# ``{`` as text, ``[]`` as an empty class), and then the two sides
# disagree on what a postcode is. So the pattern may use only this
# subset, which both engines read alike.
_ESCAPES = frozenset("dDsSwWbB") | frozenset("\\.^$|?*+()[]{}-/")
_CLASS_ESCAPES = frozenset("dDsSwW") | frozenset("\\]^[-./")
_GROUP_OPENERS = ("(?:", "(?=", "(?!", "(?<=", "(?<!")
_BOUNDS = re.compile(r"\{(\d+)(?:(,)(\d*))?\}")


def _class_end(pattern: str, start: int) -> int | None:
    """The index after the ``[...]`` class opening at ``start``, or None
    when it holds a construct the engines read differently."""
    i = start + 1
    if i < len(pattern) and pattern[i] == "^":
        i += 1
    if i < len(pattern) and pattern[i] == "]":
        return None  # ``[]`` is empty in JavaScript, a literal ``]`` in Python
    while i < len(pattern):
        char = pattern[i]
        if char == "]":
            return i + 1
        if char == "[":
            return None  # POSIX classes, nested sets
        if char == "\\":
            if i + 1 >= len(pattern) or pattern[i + 1] not in _CLASS_ESCAPES:
                return None
            i += 2
            continue
        i += 1
    return None


def non_portable_construct(pattern: str) -> str | None:
    """The first construct of ``pattern`` that Python's ``re`` and the
    storefront's JavaScript ``RegExp`` do not read alike, or None."""
    i, length = 0, len(pattern)
    repeatable = False
    while i < length:
        char = pattern[i]
        if char == "\\":
            escaped = pattern[i + 1 : i + 2]
            # A one-digit backreference, \1–\9; one to a missing group
            # fails to compile below. Longer ones are octal in JavaScript.
            next_is_digit = pattern[i + 2 : i + 3].isdigit()
            if escaped and escaped in "123456789" and not next_is_digit:
                i, repeatable = i + 2, True
                continue
            if not escaped or escaped not in _ESCAPES:
                return pattern[i : i + 2]
            i, repeatable = i + 2, escaped not in "bB"
        elif char == "[":
            end = _class_end(pattern, i)
            if end is None:
                return pattern[i:]
            i, repeatable = end, True
        elif char == "(":
            if pattern.startswith("(?", i):
                openers = (
                    o for o in _GROUP_OPENERS if pattern.startswith(o, i)
                )
                opener = next(openers, None)
                if opener is None:
                    return pattern[i : i + 3]
                i += len(opener)
            else:
                i += 1
            repeatable = False
        elif char in "*+?":
            if not repeatable:
                # Nothing to repeat, or a second quantifier: possessive.
                return pattern[max(i - 1, 0) : i + 1]
            i += 2 if pattern[i + 1 : i + 2] == "?" else 1
            repeatable = False
        elif char == "{":
            bounds = _BOUNDS.match(pattern, i)
            if not repeatable or bounds is None:
                return pattern[i : i + 4]
            low, comma, high = bounds.groups()
            if comma and high and int(high) < int(low):
                return bounds.group()
            i = bounds.end()
            i += 1 if pattern[i : i + 1] == "?" else 0
            repeatable = False
        else:
            # ``)``, ``.`` and literals can be repeated; ``^``, ``$`` and ``|`` cannot.
            repeatable = char not in "^$|"
            i += 1
    return None


def validate_postal_code_pattern(value: str) -> None:
    """Model validator: the stored pattern must compile, with the flags
    matching uses, and use only syntax the storefront reads the same way."""
    construct = non_portable_construct(value)
    if construct is not None:
        raise ValidationError(
            _(
                "Use only regular-expression syntax the storefront reads "
                "the same way: “%(construct)s” is not supported."
            ),
            params={"construct": construct},
        )
    try:
        re.compile(value, re.ASCII)
    except re.error as exc:
        raise ValidationError(
            _("Enter a valid regular expression: %(error)s"),
            params={"error": str(exc)},
        ) from exc
